#!/usr/bin/env python3
"""Independent gate on the multi-app OIG load. Trusts nothing the loader reported.

For every app in oig_apps.json it re-derives the truth from the live tenant and the drop CSV and
checks: app opted into EM · a `Role` entitlement exists with exactly that app's OWN distinct role
values · every resolvable drop identity has a grant carrying the value the drop specifies · no
grant exists for anyone absent from the drop · resolvable + orphans == drop rows. Campaigns are
deliberately out of scope (none are created in this build).

Ends in one VERDICT line; exits non-zero on any failure. Proven able to fail: flip a role in a
drop and the matching check catches it.

Usage: oig_verify_all.py [--only "NA Orion"]
"""
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).parent
ORG = "https://demo-beige-haddock-4684.okta.com"
ORGID = "00o159zwmhz6L5eo4698"
TOKEN_FILE = Path.home() / ".secrets" / "claude_3rd_party.txt"
MANIFEST = PROJ / "oig_apps.json"


def _token():
    line = TOKEN_FILE.read_text().strip().splitlines()[0].strip()
    return line.split("=", 1)[1].strip() if "=" in line else line


def call(path, ok404=False):
    req = urllib.request.Request(ORG + path)
    req.add_header("Authorization", f"SSWS {_token()}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        if e.code == 404 and ok404:
            return 404, {}
        return e.code, {}


def all_users_by_email():
    emails, url = {}, ORG + "/api/v1/users?limit=200"
    while url:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"SSWS {_token()}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req) as resp:
            for u in json.loads(resp.read()):
                e = (u.get("profile", {}).get("email") or "").strip().lower()
                if e:
                    emails[e] = u["id"]
            url = None
            for h in resp.headers.get_all("Link") or []:
                if 'rel="next"' in h:
                    url = re.search(r"<([^>]+)>", h).group(1)
    return emails


def verify_app(app, emails, failures):
    tab = app["tab"]

    def chk(label, ok, detail=""):
        if not ok:
            failures.append(f"FAIL [{tab}] {label}{' — ' + detail if detail else ''}")
        return ok

    code, live = call(f"/api/v1/apps/{app['app_id']}")
    em = live.get("settings", {}).get("emOptInStatus")
    if not chk("opted into Entitlement Management", em == "ENABLED", str(em)):
        return  # nothing downstream can exist until EM is on; one clear failure is enough

    orn = f"orn:okta:idp:{ORGID}:apps:{app['app_name']}:{app['app_id']}"
    code, ents = call("/governance/api/v1/entitlements?filter="
                      + urllib.parse.quote(f'parentResourceOrn eq "{orn}"'))
    chk("app resolves as a governance resource", code == 200, f"HTTP {code}")
    role_ent = next((e for e in ents.get("data", []) if e["name"] == "Role"), None)
    if not chk("`Role` entitlement present", role_ent is not None):
        return

    code, vals = call(f"/governance/api/v1/entitlements/{role_ent['id']}/values")
    id_to_name = {v["id"]: v["name"] for v in vals.get("data", [])}
    chk("entitlement values == this app's own distinct roles",
        sorted(id_to_name.values()) == sorted(app["roles"]),
        f"live={sorted(id_to_name.values())} expected={sorted(app['roles'])}")

    code, grants = call("/governance/api/v1/grants?filter="
                        + urllib.parse.quote(f'targetResourceOrn eq "{orn}"') + "&limit=200")
    granted = {}
    for g in grants.get("data", []):
        vids = [v["id"] for e in g.get("entitlements", []) for v in e.get("values", [])]
        granted[g["targetPrincipal"]["externalId"]] = [id_to_name.get(v, "?") for v in vids]

    rows = list(csv.DictReader((PROJ / app["drop"]).open(newline="", encoding="utf-8")))
    expected, orphans = {}, 0
    for r in rows:
        uid = emails.get(r["email"].strip().lower())
        if not uid:
            orphans += 1
            continue
        expected[uid] = r["app_role"].strip()

    mism = [(u, e, granted.get(u)) for u, e in expected.items() if granted.get(u) != [e]]
    chk("every granted value matches the role in the drop", not mism,
        f"{len(mism)} mismatched, e.g. {mism[:2]}")
    chk("every resolvable drop identity has a grant", set(expected) <= set(granted),
        f"missing {len(set(expected) - set(granted))}")
    extra = set(granted) - set(expected)
    chk("no grant for anyone absent from the drop", not extra, f"{len(extra)} extra")
    chk("coverage adds up (resolvable + orphans == rows)",
        len(expected) + orphans == len(rows), f"{len(expected)}+{orphans} vs {len(rows)}")
    print(f"  ok   {tab:<20} grants={len(granted):>4} resolvable={len(expected):>4} "
          f"orphans={orphans:>4} roles={len(id_to_name)}")


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    manifest = json.loads(MANIFEST.read_text())
    if only:
        manifest = [m for m in manifest if m["tab"] == only]

    emails = all_users_by_email()
    failures = []
    for app in manifest:
        verify_app(app, emails, failures)
    for f in failures:
        print(f)
    print(f"\nVERDICT: {'PASS' if not failures else 'FAIL'} "
          f"({len(manifest)} apps, {len(failures)} failures)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
