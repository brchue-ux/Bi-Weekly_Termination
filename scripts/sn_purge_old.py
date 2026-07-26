"""
Purge the pre-re-identity seeded sys_user records from ServiceNow (2026-07-23).
After sn_seed_users.py creates the new-identity records, the old-name records
(email = an original-worksheet UPN, now absent from the rewritten sheets) still
carry pre-scrub names. Delete them so SN reflects only current identities.

Scope guard: deletes ONLY sys_users whose email is in (old_upns - new_upns) AND
ends in @bitermtest.com — never OOB PDI users, never the fulfiller
(brandon.chue), never the integration user. Old cycle tickets that referenced
these records keep their historical values; a dangling requested_for on ~30 demo
tickets is acceptable (history, per user).

Resumable via a done-file. Independent verification is sn_reidentity_verify.py.
"""

import sys
import urllib.parse
from pathlib import Path

import base64
import urllib.request

from biweekly_recon import sn_call, SN_INSTANCE, SN_CREDS
from xlsx_min import load_workbook_rows
from reidentity import BASE, BACKUP, STARS, find_header


def sn_delete(table, sys_id):
    """DELETE returns 204 with an empty body — sn_call chokes on that, so do it here."""
    txt = SN_CREDS.read_text()
    pw = next(l.split("=", 1)[1] for l in txt.splitlines() if l.startswith("password="))
    user = next(l.strip() for l in txt.splitlines() if l.strip() and "=" not in l)
    req = urllib.request.Request(f"{SN_INSTANCE}/api/now/table/{table}/{sys_id}", method="DELETE")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    urllib.request.urlopen(req).read()

SCRATCH = Path("/tmp/claude-1000/-home-bchue/aed82bac-638b-4d9e-a003-38abeaa2d620/scratchpad")
PROGRESS = SCRATCH / "sn_purge_done.txt"
KEEP = {"brandon.chue@bitermtest.com"}  # fulfiller, created by the seed


def upn_set(book):
    out = set()
    for tab, rows in book.items():
        hi = find_header(rows)
        if hi is None:
            continue
        upn_cols = [ci for ci, name in rows[hi].items() if str(name).strip() == "TH_UPN"]
        for r in rows[hi + 1:]:
            for ci in upn_cols:
                v = str(r.get(ci, "")).strip().lower()
                if "@" in v and " " not in v:
                    out.add(v)
    return out


def main():
    old = upn_set(load_workbook_rows(BACKUP / STARS))
    new = upn_set(load_workbook_rows(BASE / STARS))
    targets = {u for u in (old - new) if u.endswith("@bitermtest.com")} - KEEP
    print(f"purge targets (old-new, bitermtest): {len(targets)}", flush=True)

    done = set(PROGRESS.read_text().split()) if PROGRESS.exists() else set()
    deleted, missing, i = 0, 0, 0
    with open(PROGRESS, "a") as prog:
        for email in sorted(targets):
            if email in done:
                continue
            i += 1
            q = urllib.parse.quote(f"email={email}", safe="=^")
            recs = sn_call("GET", f"/api/now/table/sys_user?sysparm_query={q}"
                                  "&sysparm_fields=sys_id&sysparm_limit=5")["result"]
            if not recs:
                missing += 1
            for rec in recs:
                sn_delete("sys_user", rec["sys_id"])
                deleted += 1
            prog.write(email + "\n")
            prog.flush()
            if i % 250 == 0:
                print(f"  {i}/{len(targets)} processed, {deleted} deleted", flush=True)
    print(f"DONE: {deleted} old records deleted, {missing} already absent "
          "(claim pending sn_reidentity_verify.py)", flush=True)


if __name__ == "__main__":
    main()
