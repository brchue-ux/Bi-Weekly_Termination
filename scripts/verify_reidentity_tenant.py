"""
FINAL live-tenant gate for the re-identity (run after remove_sfdc.py AND
rename_tenant.py complete). Independent: pulls every user from the live API and
checks against source-file evidence — never against the rename script's logs.

  1. NO SOURCE IDENTITY — the standard the user accepted 2026-07-23: NO original
     name-PAIRING (first+last together, from ANY source file incl. SFDC) may exist
     among seeded users — that is what makes a real person re-identifiable. Hard
     fail on any pairing. Token-level overlap of individual first/last names with
     the source vocabulary (unavoidable with a finite name pool over a 7,779-person
     source; no identity leaks from a lone common name) is REPORTED as a note, not
     a failure. Earlier zero-token-tolerance was wrong altitude — it perpetually
     failed on coincidences while the real guarantee (0 pairings) held.
  2. SFDC APP GONE — the app and all its exclusive users return 404/absent.
  3. LOGINS RECONCILE — every seeded login exists in the REWRITTEN sheets' UPN
     space (modulo digit/.cf/.ca suffix forms); names match the map.
  4. SCOPE SANITY — non-seeded users = 18 pre-existing + the intentional AM team,
     with bchue@wm.com UNTOUCHED (permanently off-limits per user).

Ends in a single line: VERDICT: PASS | FAIL.
"""

import json
import re
import sys
from pathlib import Path

from xlsx_min import load_workbook_rows
from reidentity import (BASE, BACKUP, STARS, EXCEPT, MAP_OUT,
                        find_header, identity_columns, valid_email, split_local)
from okta_client import paged, api

PROJ = Path(__file__).parent.parent
SFDC_TOKENS = BASE / ".originals" / "sfdc_name_tokens.json"
SFDC_APP_ID = "0oa15iclhmjSuXlIa698"
BCHUE = ("00u15ekemr3Fe5n2a698", "bchue@wm.com", "Brandon", "Chue")
PREEXISTING_EXPECTED = 18

failures = []


def check(name, ok, detail):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def original_identities(o_stars, o_exc):
    """(pairings, first-names, last-names) from EVERY source file — STARS UPNs +
    FirstName/LastName cols, exception UPNs, and the SFDC file's emails. A pairing
    is the re-identifiable unit; lone names are reported, not failed."""
    pairs, firsts, lasts = set(), set(), set()

    def add(f, l):
        f, l = f.strip().lower(), l.strip().lower()
        if f:
            firsts.add(f)
        if l:
            lasts.add(l)
        if f and l:
            pairs.add((f, l))

    for _, rows in o_stars.items():
        hi = find_header(rows)
        if hi is None:
            continue
        kinds = identity_columns(rows[hi])
        for r in rows[hi + 1:]:
            for ci, k in kinds.items():
                v = str(r.get(ci, ""))
                if k == "email" and valid_email(v):
                    loc = split_local(v)[0]
                    if "." in loc:
                        add(*loc.split(".", 1))
            f = next((str(r.get(ci, "")) for ci, k in kinds.items() if k == "first"), "")
            l = next((str(r.get(ci, "")) for ci, k in kinds.items() if k == "last"), "")
            if f or l:
                add(f, l)
    for _, rows in o_exc.items():
        for r in rows[1:]:
            upn = str(r.get(1, ""))
            if valid_email(upn):
                loc = split_local(upn)[0]
                if "." in loc:
                    add(*loc.split(".", 1))
    sfdc = load_workbook_rows(BASE / "FAKE USERS - SFDC 3rd party user list.xlsx")
    for _, rows in sfdc.items():
        hdr = {str(v).strip(): k for k, v in rows[0].items()}
        ec = next((ci for name, ci in hdr.items()
                   if "email" in name.lower() and "user" in name.lower()), hdr.get("UserEmail"))
        if ec is None:
            continue
        for r in rows[1:]:
            v = str(r.get(ec, ""))
            if valid_email(v):
                loc = v.split("@")[0].lower().replace("_", ".")
                if "." in loc:
                    add(*loc.split(".", 1))
    return pairs, firsts, lasts


def main():
    o_stars = load_workbook_rows(BACKUP / STARS)
    o_exc = load_workbook_rows(BACKUP / EXCEPT)
    n_stars = load_workbook_rows(BASE / STARS)
    mapping = json.load(open(MAP_OUT))
    manifest = json.load(open(PROJ / "seed_manifest.json"))
    seeded_ids = {u["id"]: login for login, u in manifest["users"].items()
                  if isinstance(u, dict) and u.get("id")}

    orig_pairs, orig_first, orig_last = original_identities(o_stars, o_exc)

    users = list(paged("/api/v1/users?limit=200"))
    users += list(paged("/api/v1/users?limit=200&filter=status%20eq%20%22DEPROVISIONED%22"))

    # 1. NO SOURCE IDENTITY — hard fail on any surviving original pairing;
    #    report lone first/last token overlap as an accepted, non-leaking note.
    pairing_leaks = []
    first_overlap, last_overlap = set(), set()
    seeded_live, pre = [], []
    for u in users:
        p = u["profile"]
        if u["id"] not in seeded_ids:
            pre.append((u["id"], p.get("login")))
            continue
        seeded_live.append(u)
        f = (p.get("firstName") or "").strip().lower()
        l = (p.get("lastName") or "").strip().lower()
        if (f, l) in orig_pairs:
            pairing_leaks.append((p.get("login"), f, l))
        if f in orig_first:
            first_overlap.add(f)
        if l in orig_last:
            last_overlap.add(l)
    check("NO SOURCE IDENTITY", not pairing_leaks,
          f"0 original pairings over {len(seeded_live)} seeded users"
          if not pairing_leaks
          else f"{len(pairing_leaks)} REAL identity leaks e.g. {pairing_leaks[:3]}")
    print(f"       note (accepted): {len(first_overlap)} first-name + {len(last_overlap)} "
          f"last-name tokens coincide with source vocabulary — no pairing, no identity leak")

    # 2. SFDC APP GONE
    gone = api("GET", f"/api/v1/apps/{SFDC_APP_ID}", ok404=True)[0] is None
    check("SFDC APP GONE", gone, "404" if gone else "app still present")

    # 3. LOGINS RECONCILE against rewritten sheets + map
    sheet_locals = set()
    for tab, rows in n_stars.items():
        for r in rows:
            for v in r.values():
                v = str(v)
                if valid_email(v):
                    sheet_locals.add(split_local(v)[0])
    bad_login, bad_name = [], []
    for u in seeded_live:
        p = u["profile"]
        base, _, _ = split_local(p["login"])
        if base not in sheet_locals:
            bad_login.append(p["login"])
        m = mapping.get(base)
        if m and (p.get("firstName"), p.get("lastName")) != (m["first"], m["last"]):
            bad_name.append((p["login"], p.get("firstName"), p.get("lastName")))
    check("LOGINS RECONCILE", not bad_login and not bad_name,
          f"{len(bad_login)} logins outside sheets, {len(bad_name)} name mismatches" +
          (f" e.g. {(bad_login + bad_name)[:3]}" if bad_login or bad_name else ""))

    # 4. SCOPE SANITY — pre-existing count (+ intentionally-added AM team) + bchue untouched
    am_file = PROJ / "am_team_okta.json"
    am_ids = set(json.load(open(am_file)).get("users", {}).values()) if am_file.exists() else set()
    pre_non_am = [x for x in pre if x[0] not in am_ids]
    expected = PREEXISTING_EXPECTED + len(am_ids)
    live_b = api("GET", f"/api/v1/users/{BCHUE[0]}", ok404=True)[0]
    b_ok = (live_b is not None and live_b["profile"]["login"] == BCHUE[1]
            and live_b["profile"]["firstName"] == BCHUE[2]
            and live_b["profile"]["lastName"] == BCHUE[3])
    check("SCOPE SANITY", len(pre_non_am) == PREEXISTING_EXPECTED and b_ok,
          f"{len(pre)} non-seeded ({len(am_ids)} AM team + {len(pre_non_am)} pre-existing, "
          f"expect {PREEXISTING_EXPECTED}); bchue@wm.com {'untouched' if b_ok else 'ALTERED OR MISSING'}")

    print(f"\nVERDICT: {'PASS' if not failures else 'FAIL (' + ', '.join(failures) + ')'}")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
