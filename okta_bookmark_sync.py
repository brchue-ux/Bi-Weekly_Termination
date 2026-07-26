#!/usr/bin/env python3
"""
Sync a non-Okta-integrated app's user roster into an Okta Bookmark app,
so an OIG Resource Campaign has something to certify against.

Pilot app: Dynamics AX NA. Reusable for any app in List of Apps.xlsx by
changing --app-label and --export.

Pipeline:
  1. Find or create a Bookmark app in Okta matching --app-label.
  2. Parse --export (.csv or .xlsx) for a column of UPN/email/username values.
  3. Resolve each identifier to a real Okta user via GET /api/v1/users/{id}.
  4. Diff against the Bookmark app's current assignments.
  5. Dry-run by default: print planned adds/removes/unmatched.
     Pass --apply to actually assign/unassign via the Okta API.

Auth: OAuth 2.0 client-credentials via a private-key-signed JWT assertion
(see okta_oauth.py) -- no long-lived admin token. Pass --client-id and
--private-key-file for an Okta API Services app that has been granted
only the scopes it needs (okta.apps.read/manage, okta.users.read) and
whose admin-role binding is scoped to a resource set of just the
Bookmark apps this script manages, not the whole org.
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

import okta_oauth

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def load_auth_header(args):
    access_token = okta_oauth.get_access_token(
        args.org.rstrip("/"), args.client_id, args.private_key_file,
        scopes=args.scopes.split(), kid=args.kid,
    )
    return f"Bearer {access_token}"


def okta_request(org, auth_header, method, path, body=None, max_retries=5):
    url = f"{org}/api/v1/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", auth_header)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw.decode(errors="replace")
            if e.code == 429 and attempt < max_retries:
                reset = e.headers.get("X-Rate-Limit-Reset")
                wait = max(1, int(reset) - int(time.time()) + 1) if reset else 2 ** attempt
                print(f"  rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            return e.code, parsed


def find_bookmark_app(org, auth_header, label):
    status, apps = okta_request(org, auth_header, "GET", f"apps?q={urllib.parse.quote(label)}")
    if status != 200:
        sys.exit(f"App lookup failed ({status}): {apps}")
    for app in apps:
        if app.get("label") == label:
            return app
    return None


def create_bookmark_app(org, auth_header, label, url):
    body = {
        "name": "bookmark",
        "label": label,
        "signOnMode": "BOOKMARK_SSO",
        "settings": {"app": {"url": url, "requestIntegration": False}},
    }
    status, app = okta_request(org, auth_header, "POST", "apps", body)
    if status != 200:
        sys.exit(f"App creation failed ({status}): {app}")
    print(f"Created Bookmark app '{label}' -> id {app['id']}")
    return app


def get_current_assignments(org, auth_header, app_id):
    assigned = {}
    path = f"apps/{app_id}/users?limit=500"
    while path:
        status, users = okta_request(org, auth_header, "GET", path)
        if status != 200:
            sys.exit(f"Failed to list app users ({status}): {users}")
        for u in users:
            assigned[u["id"]] = u
        path = None  # pagination via Link header not handled; fine for pilot-scale rosters
    return assigned


def resolve_user(org, auth_header, identifier):
    status, user = okta_request(org, auth_header, "GET", f"users/{urllib.parse.quote(identifier)}")
    if status == 200:
        return user
    return None


def parse_csv(path, column):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        col = column or reader.fieldnames[0]
        return [row[col].strip() for row in reader if row.get(col, "").strip()]


def _col_to_idx(ref):
    letters = re.match(r"[A-Z]+", ref).group()
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def parse_xlsx(path, column):
    z = zipfile.ZipFile(path)
    sst = []
    if "xl/sharedStrings.xml" in z.namelist():
        with z.open("xl/sharedStrings.xml") as f:
            tree = ET.parse(f)
            for si in tree.getroot().findall("m:si", NS):
                texts = si.findall(".//m:t", NS)
                sst.append("".join(t.text or "" for t in texts))

    with z.open("xl/worksheets/sheet1.xml") as f:
        tree = ET.parse(f)
        sheetdata = tree.getroot().find("m:sheetData", NS)
        rows = []
        for row in sheetdata.findall("m:row", NS):
            rowvals = {}
            maxc = 0
            for c in row.findall("m:c", NS):
                idx = _col_to_idx(c.get("r"))
                maxc = max(maxc, idx)
                t = c.get("t")
                v = c.find("m:v", NS)
                val = v.text if v is not None else None
                if t == "s" and val is not None:
                    val = sst[int(val)]
                rowvals[idx] = val
            rows.append([rowvals.get(i, "") for i in range(maxc + 1)])

    header, data_rows = rows[0], rows[1:]
    col_idx = header.index(column) if column else 0
    return [r[col_idx].strip() for r in data_rows if len(r) > col_idx and r[col_idx].strip()]


def parse_export(path, column):
    if path.lower().endswith(".xlsx"):
        return parse_xlsx(path, column)
    return parse_csv(path, column)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", required=True, help="e.g. https://yourorg.okta.com")
    ap.add_argument("--client-id", required=True, help="Okta API Services app client_id")
    ap.add_argument("--private-key-file", required=True,
                     help="PEM file with the private key registered on the API Services app")
    ap.add_argument("--kid", default="term-revamp-key-1",
                     help="key id of the registered JWK, must match the app's jwks.keys[].kid")
    ap.add_argument("--scopes", default=" ".join(okta_oauth.DEFAULT_SCOPES),
                     help="space-separated OAuth scopes to request")
    ap.add_argument("--app-label", default="Dynamics AX NA (Bi-Weekly Term Test)")
    ap.add_argument("--app-url", default="https://internal.example/dynamics-ax-na",
                     help="placeholder URL used only if the Bookmark app must be created")
    ap.add_argument("--export", required=True, help="path to .csv or .xlsx roster export")
    ap.add_argument("--column", help="column name holding UPN/email/username (default: first column)")
    ap.add_argument("--apply", action="store_true", help="actually assign/unassign; default is dry-run")
    args = ap.parse_args()

    auth_header = load_auth_header(args)
    org = args.org.rstrip("/")

    app_exists = True
    app = find_bookmark_app(org, auth_header, args.app_label)
    if app is None:
        app_exists = False
        print(f"No existing Bookmark app found for '{args.app_label}'.")
        if args.apply:
            app = create_bookmark_app(org, auth_header, args.app_label, args.app_url)
        else:
            print("(dry-run) would create it here")

    identifiers = parse_export(args.export, args.column)
    print(f"Parsed {len(identifiers)} identifiers from {args.export}")

    # Resolution against real Okta users doesn't depend on the Bookmark app
    # existing yet, so it runs on every dry-run -- not just re-syncs of an
    # app that's already been created.
    resolved = {}
    unmatched = []
    for ident in identifiers:
        user = resolve_user(org, auth_header, ident)
        if user:
            resolved[user["id"]] = ident
        else:
            unmatched.append(ident)

    # A not-yet-created app has no assignments yet, so the diff is simply
    # "everything resolved is new" -- fetching real assignments only
    # matters once the app (and thus its roster) actually exists.
    current = get_current_assignments(org, auth_header, app["id"]) if app_exists or args.apply else {}

    to_add = set(resolved) - set(current)
    to_remove = set(current) - set(resolved)

    print(f"\nTo add:    {len(to_add)}")
    print(f"To remove: {len(to_remove)}")
    print(f"Unmatched (in export, no Okta user found): {len(unmatched)}")
    for ident in unmatched:
        print(f"  - {ident}")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to make changes.")
        return

    for uid in to_add:
        status, resp = okta_request(org, auth_header, "POST", f"apps/{app['id']}/users",
                                     {"id": uid, "scope": "USER"})
        print(f"assign {resolved[uid]} ({uid}): {status}")

    for uid in to_remove:
        status, resp = okta_request(org, auth_header, "DELETE", f"apps/{app['id']}/users/{uid}")
        print(f"unassign {uid}: {status}")


if __name__ == "__main__":
    main()
