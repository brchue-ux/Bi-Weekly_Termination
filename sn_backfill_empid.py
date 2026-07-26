"""
Backfill sys_user.employee_number from the roster's TH_EmployeeID (2026-07-23).

The seed created SN person records with name/email/title/manager but never wrote
employee_number, so the Employee ID field (now shown on the sys_user form) was
empty for all 2,040 records. The recon findings and the removal-ticket variables
both carry employee_id, so having it on the person record closes the loop:
a fulfiller can match the ticket's employee id to the profile.

Keyed on email (the full login) because sys_user.user_name truncates at 40 chars.
Only touches @bitermtest.com records that have a roster employee id. Idempotent —
skips records whose employee_number already matches. Resumable via a done-file.
"""

import sys
import urllib.parse
from pathlib import Path

from biweekly_recon import sn_call
from xlsx_min import load_workbook_rows
from reidentity import BASE, STARS, find_header

SCRATCH = Path("/tmp/claude-1000/-home-bchue/aed82bac-638b-4d9e-a003-38abeaa2d620/scratchpad")
PROGRESS = SCRATCH / "sn_empid_done.txt"


def roster_empids():
    """upn(lower) -> TH_EmployeeID, from the CURRENT (re-identified) worksheets."""
    out = {}
    for _, rows in load_workbook_rows(BASE / STARS).items():
        hi = find_header(rows)
        if hi is None:
            continue
        hdr = {str(v).strip(): ci for ci, v in rows[hi].items()}
        upn_c, eid_c = hdr.get("TH_UPN"), hdr.get("TH_EmployeeID")
        if upn_c is None or eid_c is None:
            continue
        for r in rows[hi + 1:]:
            upn = str(r.get(upn_c, "")).strip().lower()
            eid = str(r.get(eid_c, "")).strip()
            if "@" in upn and " " not in upn and eid and eid != "1":
                out.setdefault(upn, eid)
    return out


def main():
    want = roster_empids()
    print(f"roster employee ids: {len(want)}", flush=True)

    # pull existing SN records once (email -> sys_id, current employee_number)
    existing, offset = {}, 0
    while True:
        q = urllib.parse.quote("emailLIKEbitermtest.com", safe="=^")
        page = sn_call("GET", f"/api/now/table/sys_user?sysparm_query={q}"
                              f"&sysparm_fields=sys_id,email,employee_number"
                              f"&sysparm_limit=1000&sysparm_offset={offset}")["result"]
        if not page:
            break
        for r in page:
            if r.get("email"):
                existing[r["email"].lower()] = (r["sys_id"], r.get("employee_number") or "")
        offset += len(page)
    print(f"SN person records: {len(existing)}", flush=True)

    done = set(PROGRESS.read_text().split()) if PROGRESS.exists() else set()
    updated = skipped = missing = 0
    with open(PROGRESS, "a") as prog:
        for upn, eid in sorted(want.items()):
            if upn in done:
                continue
            rec = existing.get(upn)
            if not rec:
                missing += 1
                continue
            sys_id, current = rec
            if current == eid:
                skipped += 1
                continue
            sn_call("PATCH", f"/api/now/table/sys_user/{sys_id}", {"employee_number": eid})
            prog.write(upn + "\n")
            prog.flush()
            updated += 1
            if updated % 250 == 0:
                print(f"  {updated} updated", flush=True)
    print(f"DONE: {updated} updated, {skipped} already correct, {missing} not in SN "
          "(claim pending independent re-query)", flush=True)


if __name__ == "__main__":
    main()
