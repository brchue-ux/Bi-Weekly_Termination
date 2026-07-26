"""
Backfill sys_user.title (job title) and .country from the roster (2026-07-23).

Source: the STARS workbook's "TalentHub - Invalid UPN" tab — despite its name it
is the HR event log and it DOES join to our seeded population (TH_UPN /
TH_BusinessEmail), carrying TH_JobTitle, TH_CountryName, TH_CountryCode. Multiple
rows exist per person (one per HR event), so we take the row with the LATEST
TH_EventDate — a person's current title, not a stale one.

Why overwrite title: the seed wrote a placeholder ("Manager" or empty). Manager
status is already expressed properly by the manager hierarchy (their reports point
at them), so real job titles are strictly better and make the profiles believable.

Only fields we genuinely have data for are touched — no invented values.
Keyed on email (user_name truncates at 40 chars). Idempotent, resumable.
"""

import sys
import urllib.parse
from pathlib import Path

from biweekly_recon import sn_call
from xlsx_min import load_workbook_rows
from reidentity import BASE, STARS, find_header

SCRATCH = Path("/tmp/claude-1000/-home-bchue/aed82bac-638b-4d9e-a003-38abeaa2d620/scratchpad")
PROGRESS = SCRATCH / "sn_profile_done.txt"
TH_TAB = "TalentHub - Invalid UPN"


def seeded_upns(book):
    """UPNs on the 10 app tabs = the people who actually exist as SN person records."""
    out = set()
    for tab, rows in book.items():
        if tab in (TH_TAB, "Header"):
            continue
        hi = find_header(rows)
        if hi is None:
            continue
        hdr = {str(v).strip(): ci for ci, v in rows[hi].items()}
        c = hdr.get("TH_UPN")
        if c is None:
            continue
        for r in rows[hi + 1:]:
            u = str(r.get(c, "")).strip().lower()
            if "@" in u and " " not in u:
                out.add(u)
    return out


def hr_attributes(book, wanted):
    """email -> {title, country} from the LATEST HR event row for that person."""
    rows = book.get(TH_TAB)
    hi = find_header(rows) if rows else None
    if hi is None:
        return {}
    hdr = {str(v).strip(): ci for ci, v in rows[hi].items()}
    best = {}
    for r in rows[hi + 1:]:
        upn = str(r.get(hdr.get("TH_UPN", -1), "")).strip().lower()
        email = str(r.get(hdr.get("TH_BusinessEmail", -1), "")).strip().lower()
        key = upn if upn in wanted else (email if email in wanted else None)
        if not key:
            continue
        try:                       # TH_EventDate is an Excel serial; latest wins
            when = float(str(r.get(hdr.get("TH_EventDate", -1), "") or 0) or 0)
        except ValueError:
            when = 0.0
        title = str(r.get(hdr.get("TH_JobTitle", -1), "")).strip()
        country = str(r.get(hdr.get("TH_CountryName", -1), "")).strip()
        if not title and not country:
            continue
        if key not in best or when >= best[key][0]:
            best[key] = (when, title, country)
    return {k: {"title": t, "country": c} for k, (_, t, c) in best.items()}


def main():
    book = load_workbook_rows(BASE / STARS)
    wanted = seeded_upns(book)
    attrs = hr_attributes(book, wanted)
    print(f"seeded people: {len(wanted)} | with HR title/country: {len(attrs)}", flush=True)

    existing, offset = {}, 0
    while True:
        q = urllib.parse.quote("emailLIKEbitermtest.com", safe="=^")
        page = sn_call("GET", f"/api/now/table/sys_user?sysparm_query={q}"
                              f"&sysparm_fields=sys_id,email,title,country"
                              f"&sysparm_limit=1000&sysparm_offset={offset}")["result"]
        if not page:
            break
        for r in page:
            if r.get("email"):
                existing[r["email"].lower()] = r
        offset += len(page)
    print(f"SN person records: {len(existing)}", flush=True)

    done = set(PROGRESS.read_text().split()) if PROGRESS.exists() else set()
    updated = skipped = missing = 0
    with open(PROGRESS, "a") as prog:
        for email, a in sorted(attrs.items()):
            if email in done:
                continue
            rec = existing.get(email)
            if not rec:
                missing += 1
                continue
            payload = {}
            if a["title"] and rec.get("title") != a["title"]:
                payload["title"] = a["title"]
            if a["country"] and rec.get("country") != a["country"]:
                payload["country"] = a["country"]
            if not payload:
                skipped += 1
                continue
            sn_call("PATCH", f"/api/now/table/sys_user/{rec['sys_id']}", payload)
            prog.write(email + "\n")
            prog.flush()
            updated += 1
            if updated % 250 == 0:
                print(f"  {updated} updated", flush=True)
    print(f"DONE: {updated} updated, {skipped} already correct, {missing} not in SN "
          "(claim pending independent re-query)", flush=True)


if __name__ == "__main__":
    main()
