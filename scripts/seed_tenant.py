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
import urllib.parse
from pathlib import Path

import biterm_config
import biterm_creds
import biterm_http
import biterm_runlog as runlog
# Domain vocabulary lives in biterm_domain, not here. These names are re-exported for the
# handful of modules that still import them from this file; new code imports biterm_domain.
from biterm_domain import (APP_LABEL_PREFIX, NO_UPN, NO_UPN_SENTINEL, SFDC_APP,  # noqa: F401
                           STARS_TABS, valid_login)
from xlsx_min import column, column_by_suffix, find_header_row, load_workbook_rows

ORG = biterm_config.org()
TOKEN_FILE = biterm_config.get("admin_token_file")
BASE = Path(__file__).resolve().parent.parent / "App User Lists"
MANIFEST = Path(__file__).resolve().parent.parent / "seed_manifest.json"


# ---------------------------------------------------------------- plan building

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
        hdr_idx, headers = find_header_row(rows, ["TH_UPN", "TH_EmployeeStatus"], sheet_name=tab)
        upn_c = column(headers, "TH_UPN", sheet_name=tab)
        st_c = column(headers, "TH_EmployeeStatus", sheet_name=tab)
        label = APP_LABEL_PREFIX + tab
        apps[label], orphans[label] = [], 0
        for r in rows[hdr_idx + 1:]:
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
    sfdc_tab, rows = next(iter(sfdc.items()))
    hdr_idx, headers = find_header_row(rows, ["UserEmail"], sheet_name=sfdc_tab)
    # FirstName/LastName are de-id scrambled: never import them
    em_c = column(headers, "UserEmail", sheet_name=sfdc_tab)
    apps[SFDC_APP], orphans[SFDC_APP] = [], 0
    for r in rows[hdr_idx + 1:]:
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

_client = None


def client():
    """Shared HTTP client (timeouts, retry ladder, typed errors, change log).

    seed_tenant deliberately keeps the privileged SSWS token: it is scaffolding that
    creates the demo tenant. The detective control runs under the least-privilege OAuth
    service app and must never use this credential.
    """
    global _client
    if _client is None:
        _client = biterm_http.okta_client(
            biterm_http.ssws(lambda: biterm_creds.api_token(TOKEN_FILE)),
            on_write=runlog.change_recorder("seed_tenant", dry_run=False))
    return _client


def api(method, path, body=None, ok404=False):
    """Single Okta call. Returns (parsed JSON, headers); (None, headers) on 404 when ok404."""
    status, parsed, headers = client().request(
        method, path, body, allow_statuses=(404,) if ok404 else ())
    if ok404 and status == 404:
        return None, headers
    return parsed, headers


def paged(path):
    """Yield items across Okta link-header pagination."""
    yield from client().paged(path)


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
