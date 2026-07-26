#!/usr/bin/env python3
"""Seed the demo Okta tenant with the roster apps and users.

Creates one Bookmark app per certifiable population (10 STARS tabs + SFDC/DocuSign),
an Okta user per unique roster identity, and the app assignments — so the 3-way
join (app roster <-> HR <-> Okta) has a real Okta leg to query.

Deliberate test surface (all deterministic, recorded in the manifest):
  - Roster rows with no usable UPN ("Not found in TalentHub") get NO Okta user:
    they are the app-side orphans the report must catch.
  - Terminated/Retired identities are split by hash: ~40% left ACTIVE in Okta
    (the un-deprovisioned failure mode), ~30% SUSPENDED, ~30% never created —
    covering every branch of the "enabled / disabled / nonexistent" requirement.
  - SFDC seats are created regardless of seat status (73% are "Closed"; omitting
    them would contradict "each user on the app"). Seat status is app-side state
    the report reads from the roster, not from Okta.

Idempotent: existing users (by login), apps (by label), and assignments are
skipped, so a killed run can simply be re-run. Progress goes to stderr; the
created-state manifest goes to seed_manifest.json next to this script.

Usage: seed_tenant.py [--dry-run]   (token: ~/.secrets/claude_3rd_party.txt)
"""
import hashlib
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from xlsx_min import load_workbook_rows

ORG = "https://demo-beige-haddock-4684.okta.com"
TOKEN_FILE = Path.home() / ".secrets" / "claude_3rd_party.txt"
BASE = Path(__file__).parent / "App User Lists"
MANIFEST = Path(__file__).parent / "seed_manifest.json"

STARS_TABS = ["NA Apollo", "NA Stellar", "NA Orion", "NA Saturn East", "NA Saturn Central",
              "NA Saturn West", "NA Saturn ComSat", "NA Saturn Corp", "CloudForce HQ", "CloudForce Canada"]
SFDC_APP = "SFDC 3rd Party (DocuSign)"
NO_UPN = "Not found in TalentHub"
APP_LABEL_PREFIX = "BiTerm - "  # namespaces seeded apps away from pre-existing demo apps


# ---------------------------------------------------------------- plan building

def valid_login(s: str) -> bool:
    return "@" in s and " " not in s and s != NO_UPN.lower()


def terminated_fate(login: str) -> str:
    """Deterministic split for Terminated/Retired identities."""
    h = int(hashlib.sha256(login.encode()).hexdigest(), 16) % 10
    return "active" if h < 4 else ("suspended" if h < 7 else "absent")


def build_plan():
    """Return (users, apps) where users[login] = {first,last,fate} and
    apps[label] = [login, ...]. Also counts skipped orphan rows per app."""
    users, apps, orphans = {}, {}, {}

    stars = load_workbook_rows(BASE / "FAKE USERS - STARS Report.xlsx")
    for tab in STARS_TABS:
        rows = stars[tab]
        cols = {v: k for k, v in rows[1].items()}
        upn_c, st_c = cols["TH_UPN"], cols["TH_EmployeeStatus"]
        label = APP_LABEL_PREFIX + tab
        apps[label], orphans[label] = [], 0
        for r in rows[2:]:
            if not any(str(v).strip() for v in r.values()):
                continue
            login = (r.get(upn_c) or "").strip().lower()
            if not valid_login(login):
                orphans[label] += 1
                continue
            st = (r.get(st_c) or "").strip()
            u = users.setdefault(login, {"first": "", "last": "", "hr": st})
            if st in ("Terminated", "Retired"):  # worst status seen wins
                u["hr"] = st
            apps[label].append(login)

    sfdc = load_workbook_rows(BASE / "FAKE USERS - SFDC 3rd party user list.xlsx")
    rows = next(iter(sfdc.values()))
    cols = {v: k for k, v in rows[0].items()}
    em_c = cols["UserEmail"]  # FirstName/LastName are de-id scrambled: never import them
    apps[SFDC_APP], orphans[SFDC_APP] = [], 0
    for r in rows[1:]:
        login = (r.get(em_c) or "").strip().lower()
        if not valid_login(login):
            orphans[SFDC_APP] += 1
            continue
        users.setdefault(login, {"first": "", "last": "", "hr": ""})
        apps[SFDC_APP].append(login)

    for login, u in users.items():
        # names always derive from the login's local part (canonical rule)
        parts = login.split("@")[0].replace("_", ".").split(".")
        u["first"], u["last"] = parts[0].title(), (parts[-1].title() if len(parts) > 1 else "User")
        if u["hr"] in ("Terminated", "Retired"):
            u["fate"] = terminated_fate(login)
        else:  # Active / leave / SFDC seats / name-in-status unknowns: present and enabled
            u["fate"] = "active"
    return users, apps, orphans


# ---------------------------------------------------------------- okta client

def api(method, path, body=None, ok404=False):
    """Single Okta call with 429 backoff. Returns parsed JSON (or None on 404 when ok404)."""
    token = TOKEN_FILE.read_text().strip()
    url = path if path.startswith("http") else ORG + path
    for _ in range(6):
        req = urllib.request.Request(url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", f"SSWS {token}")
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                return (json.loads(data) if data else {}), resp.headers
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok404:
                return None, e.headers
            if e.code == 429:
                reset = int(e.headers.get("X-Rate-Limit-Reset", time.time() + 30))
                wait = max(reset - time.time(), 1) + 1
                print(f"    429; sleeping {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}") from e
    raise RuntimeError(f"{method} {path}: rate-limited past retries")


def paged(path):
    """Yield items across Okta link-header pagination."""
    url = ORG + path
    while url:
        items, headers = api("GET", url)
        yield from items
        url = None
        for link in headers.get_all("link") or []:
            if 'rel="next"' in link:
                url = link[link.index("<") + 1:link.index(">")]


# ---------------------------------------------------------------- execution

def main():
    dry = "--dry-run" in sys.argv
    users, apps, orphans = build_plan()
    fates = {"active": 0, "suspended": 0, "absent": 0}
    for u in users.values():
        fates[u["fate"]] += 1
    n_assign = sum(len({l for l in logins if users[l]["fate"] != "absent"}) for logins in apps.values())
    print(f"PLAN: {len(users)} identities (create {fates['active']} active, "
          f"{fates['suspended']} suspended, omit {fates['absent']}), "
          f"{len(apps)} apps, ~{n_assign} assignments; "
          f"orphan rows skipped: {sum(orphans.values())}", file=sys.stderr)
    if dry:
        for label in apps:
            print(f"  {label}: {len(apps[label])} roster rows, {orphans[label]} orphan rows", file=sys.stderr)
        return

    print("Fetching existing tenant state...", file=sys.stderr)
    existing_users = {u["profile"]["login"].lower(): u["id"] for u in paged("/api/v1/users?limit=200")}
    existing_apps = {a["label"]: a["id"] for a in paged("/api/v1/apps?limit=200")}

    manifest = {"org": ORG, "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "users": {}, "apps": {}, "orphan_rows_skipped": orphans}

    # users
    todo = {l: u for l, u in users.items() if u["fate"] != "absent"}
    for i, (login, u) in enumerate(sorted(todo.items()), 1):
        if login in existing_users:
            uid = existing_users[login]
        else:
            created, _ = api("POST", "/api/v1/users?activate=true", {
                "profile": {"firstName": u["first"], "lastName": u["last"],
                            "email": login, "login": login},
                # digits-only tail: satisfies all char classes via the prefix and can
                # never contain "part of username" (Okta rejects alpha name fragments)
                "credentials": {"password": {"value": f"Aa1!{secrets.randbelow(10**24):024d}"}},
            })
            uid = created["id"]
            existing_users[login] = uid
        if u["fate"] == "suspended":
            status, _ = api("GET", f"/api/v1/users/{uid}")
            if status["status"] != "SUSPENDED":
                api("POST", f"/api/v1/users/{uid}/lifecycle/suspend")
        manifest["users"][login] = {"id": uid, "fate": u["fate"], "hr": u["hr"]}
        if i % 200 == 0 or i == len(todo):
            print(f"  users {i}/{len(todo)}", file=sys.stderr)
            MANIFEST.write_text(json.dumps(manifest, indent=1))

    # apps + assignments
    for label, logins in apps.items():
        if label in existing_apps:
            app_id = existing_apps[label]
        else:
            created, _ = api("POST", "/api/v1/apps", {
                "name": "bookmark", "label": label, "signOnMode": "BOOKMARK",
                "settings": {"app": {"url": f"https://bitermtest.example.com/{urllib.parse.quote(label)}"}},
            })
            app_id = created["id"]
        assigned = {au["id"] for au in paged(f"/api/v1/apps/{app_id}/users?limit=200")}
        want = sorted({l for l in logins if users[l]["fate"] != "absent"})
        done = 0
        for login in want:
            uid = existing_users[login]
            if uid not in assigned:
                api("PUT", f"/api/v1/apps/{app_id}/users/{uid}", {"id": uid, "scope": "USER"})
            done += 1
            if done % 200 == 0:
                print(f"  {label}: {done}/{len(want)} assigned", file=sys.stderr)
        manifest["apps"][label] = {"id": app_id, "assigned": len(want)}
        print(f"  {label}: {len(want)} assignments done", file=sys.stderr)
        MANIFEST.write_text(json.dumps(manifest, indent=1))

    print("SEED COMPLETE", file=sys.stderr)


if __name__ == "__main__":
    main()
