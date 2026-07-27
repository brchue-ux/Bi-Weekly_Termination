#!/usr/bin/env python3
"""
Sync a non-Okta-integrated app's user roster into an Okta Bookmark app,
so an OIG Resource Campaign has something to certify against.

Pilot app: Dynamics AX NA. Reusable for any app in List of Apps.xlsx by
changing --app-label and --export.

Pipeline:
  1. Find or create a Bookmark app in Okta matching --app-label.
  2. Parse --export (.csv or .xlsx) for a column of UPN/email/username values.
  3. Resolve each identifier to a real Okta user.
  4. Diff against the Bookmark app's current assignments.
  5. Dry-run by default: print planned adds/removes/unmatched.
     Pass --apply to actually assign/unassign via the Okta API.

Auth: OAuth 2.0 client-credentials via a private-key-signed JWT assertion
(see okta_oauth.py) -- no long-lived admin token.

THIS SCRIPT CAN REMOVE ACCESS. Three defects on that path were fixed 2026-07-26; read
before changing anything here.

  1. FAIL-CLOSED RESOLUTION. `resolve_user` returned None for a 429, a 500 and a 503
     exactly as it did for a genuine 404. None meant "not in the roster's resolved set",
     which meant the user landed in `to_remove`, which under --apply issued a DELETE. A
     rate limit deprovisioned people. Resolution now returns an explicit FOUND /
     NOT_FOUND / UNKNOWN, and a single UNKNOWN aborts the run before any write.

  2. THE PARSER. This module used to carry its own xlsx reader that opened
     `xl/worksheets/sheet1.xml` directly — which is not necessarily the workbook's first
     sheet, and was silently empty for 9 of 10 STARS tabs. An empty parse produced an
     empty `resolved`, and `to_remove = current - resolved` is then EVERYONE. It now uses
     xlsx_min, which resolves sheets through the workbook relationships.

  3. PAGINATION. `get_current_assignments` fetched one page of 500 and returned it as the
     complete set, with a comment saying pagination was "fine for pilot-scale rosters".
     A wrong `current` is a wrong diff in both directions.

The blast-radius guard itself lives in `run_all.py::guard_removals`, which is the
supported entrypoint.
"""

import argparse
import csv
import sys

import biterm_config
import biterm_http
import okta_oauth
from xlsx_min import column, find_header_row, load_workbook_rows

FOUND, NOT_FOUND, UNKNOWN = "FOUND", "NOT_FOUND", "UNKNOWN"


class RosterError(RuntimeError):
    """The export cannot be parsed into a trustworthy list of identifiers."""


class UnsafeSyncError(RuntimeError):
    """The computed change set is not safe to apply."""


def build_client(args):
    """An authenticated client that re-mints its bearer token as it expires.

    The token used to be minted once at startup and never refreshed; a long run (this
    script issues one call per roster row) outlived it and 401'd mid-mutation.
    """
    org = args.org.rstrip("/")
    state = {"token": None, "expires": 0.0}

    def token():
        import time
        if state["token"] is None or time.time() >= state["expires"]:
            payload = okta_oauth.fetch_token(org, args.client_id, args.private_key_file,
                                             scopes=args.scopes.split(), kid=args.kid)
            lifetime = int(payload.get("expires_in", 3600))
            state["token"] = payload["access_token"]
            state["expires"] = time.time() + lifetime - min(300, lifetime // 2)
        return state["token"]

    return biterm_http.Client(org, biterm_http.bearer(token),
                              error_class=biterm_http.OktaApiError)


def find_bookmark_app(client, label):
    import urllib.parse
    apps = client.get_json(f"/api/v1/apps?q={urllib.parse.quote(label)}&limit=200")
    for app in apps:
        if app.get("label") == label:
            return app
    return None


def create_bookmark_app(client, label, url):
    body = {
        "name": "bookmark",
        "label": label,
        "signOnMode": "BOOKMARK",   # matches seed_tenant.py; "BOOKMARK_SSO" was inconsistent
        "settings": {"app": {"url": url, "requestIntegration": False}},
    }
    _, app, _ = client.request("POST", "/api/v1/apps", body)
    print(f"Created Bookmark app '{label}' -> id {app['id']}")
    return app


def get_current_assignments(client, app_id):
    """Every assignee, across ALL pages. A partial read here is a wrong diff."""
    return {u["id"]: u for u in client.paged(f"/api/v1/apps/{app_id}/users?limit=200")}


def resolve_user(client, identifier):
    """(state, user). Never conflates "no such user" with "could not ask"."""
    import urllib.parse
    try:
        status, user, _ = client.request(
            "GET", f"/api/v1/users/{urllib.parse.quote(identifier)}", allow_statuses=(404,))
    except biterm_http.HttpError as e:
        return UNKNOWN, {"error": str(e)}
    if status == 404:
        return NOT_FOUND, None
    return FOUND, user


def resolve_all(client, identifiers):
    """Resolve the whole roster, then refuse to continue if anything is UNKNOWN."""
    resolved, unmatched, unknown = {}, [], []
    for ident in identifiers:
        state, user = resolve_user(client, ident)
        if state == FOUND:
            resolved[user["id"]] = ident
        elif state == NOT_FOUND:
            unmatched.append(ident)
        else:
            unknown.append((ident, user.get("error", "")))
    if unknown:
        detail = "; ".join(f"{i}: {e}" for i, e in unknown[:5])
        raise UnsafeSyncError(
            f"{len(unknown)} identifier(s) could not be resolved because the API did not "
            f"answer (not because the user is absent). Treating them as absent would put "
            f"them in the removal set. Aborting before any write. {detail}")
    return resolved, unmatched


def parse_csv(path, column_name):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RosterError(f"{path}: no header row")
        col = column_name or reader.fieldnames[0]
        if col not in reader.fieldnames:
            raise RosterError(f"{path}: column {col!r} not found; have {reader.fieldnames}")
        return [row[col].strip() for row in reader if (row.get(col) or "").strip()]


def parse_xlsx(path, column_name):
    """First sheet of the workbook, resolved through the workbook relationships."""
    book = load_workbook_rows(path)
    if not book:
        raise RosterError(f"{path}: workbook has no sheets")
    sheet_name, rows = next(iter(book.items()))
    if column_name:
        hdr_idx, headers = find_header_row(rows, [column_name], sheet_name=sheet_name)
        col_idx = column(headers, column_name, sheet_name=sheet_name)
    else:
        hdr_idx = 0
        first = rows[0] if rows else {}
        if not first:
            raise RosterError(f"{path}!{sheet_name}: first row is empty; pass --column")
        col_idx = min(first)
    out = []
    for r in rows[hdr_idx + 1:]:
        val = str(r.get(col_idx, "") or "").strip()
        if val:
            out.append(val)
    return out


def parse_export(path, column_name):
    ids = parse_xlsx(path, column_name) if path.lower().endswith(".xlsx") \
        else parse_csv(path, column_name)
    if not ids:
        # An empty roster is a parse failure, never an instruction to unassign everyone.
        raise RosterError(
            f"{path}: parsed ZERO identifiers. Refusing to treat an empty roster as "
            f"'remove all assignees'. Check the file and the --column value.")
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", default=biterm_config.get("org"), help="e.g. https://yourorg.okta.com")
    ap.add_argument("--client-id", default=biterm_config.get("client_id"))
    ap.add_argument("--private-key-file", default=biterm_config.get("private_key_file"),
                    help="PEM file with the private key registered on the API Services app")
    ap.add_argument("--kid", default=biterm_config.get("kid"))
    ap.add_argument("--scopes", default=" ".join(okta_oauth.DEFAULT_SCOPES),
                    help="space-separated OAuth scopes to request")
    ap.add_argument("--app-label", required=True)
    ap.add_argument("--app-url", default="https://internal.example/placeholder",
                    help="placeholder URL used only if the Bookmark app must be created")
    ap.add_argument("--export", required=True, help="path to .csv or .xlsx roster export")
    ap.add_argument("--column", help="column name holding UPN/email/username (default: first column)")
    ap.add_argument("--apply", action="store_true", help="actually assign/unassign; default is dry-run")
    args = ap.parse_args()

    client = build_client(args)

    app = find_bookmark_app(client, args.app_label)
    app_exists = app is not None
    if not app_exists:
        print(f"No existing Bookmark app found for '{args.app_label}'.")
        if args.apply:
            app = create_bookmark_app(client, args.app_label, args.app_url)
        else:
            print("(dry-run) would create it here")

    identifiers = parse_export(args.export, args.column)
    print(f"Parsed {len(identifiers)} identifiers from {args.export}")

    resolved, unmatched = resolve_all(client, identifiers)
    current = get_current_assignments(client, app["id"]) if (app_exists or args.apply) else {}

    to_add = set(resolved) - set(current)
    to_remove = set(current) - set(resolved)

    print(f"\nTo add:    {len(to_add)}")
    print(f"To remove: {len(to_remove)}")
    print(f"Unmatched (in export, confirmed absent from Okta): {len(unmatched)}")
    for ident in unmatched:
        print(f"  - {ident}")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to make changes.")
        return 0

    for uid in sorted(to_add):
        status, _, _ = client.request("POST", f"/api/v1/apps/{app['id']}/users",
                                      {"id": uid, "scope": "USER"})
        print(f"assign {resolved[uid]} ({uid}): {status}")
    for uid in sorted(to_remove):
        status, _, _ = client.request("DELETE", f"/api/v1/apps/{app['id']}/users/{uid}",
                                      ok_statuses=(200, 204))
        print(f"unassign {uid}: {status}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RosterError, UnsafeSyncError, biterm_http.HttpError, okta_oauth.OAuthError) as e:
        sys.exit(f"ABORTED: {e}")
