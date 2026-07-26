#!/usr/bin/env python3
"""Generate the mock biweekly scheduled-drop files for the Okta Workflows design.

Two feeds land per cycle; Okta is the third leg of the join and needs no file at all:

    <App Name>/<App>_users_<YYYYMMDD>.csv        app-native roster only (NO HR columns)
    _HR_TalentHub/TalentHub_HR_<YYYYMMDD>.csv    authoritative HR feed

Why the app drops are UNJOINED: the real STARS workbook arrives with TH_* columns already
sitting beside the app's own columns, i.e. somebody performed the app<->HR join before the
file existed. That join is precisely what Workflows automates, so a mock of the future-state
drop must not carry it. An app knows its own accounts; it has no idea who is terminated.

One folder per app because that folder is the unit a SCIM connector eventually replaces --
onboard three apps and three folders stop receiving drops while the rest keep going.

Deterministic (SEED below): re-run to regenerate rather than hand-editing any file.
Writes MANIFEST.json naming the exact identities behind every seeded showcase case, so the
demo can point at a specific row instead of asserting a branch exists.
"""
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

from seed_tenant import STARS_TABS, NO_UPN
from xlsx_min import load_workbook_rows

PROJ = Path(__file__).parent.parent
SRC = PROJ / "App User Lists"
OUT = PROJ / "bi-weekly term and app list"
SEED = "biterm-drops-20260723"

CYCLE1 = dt.date(2026, 7, 23)
CYCLE2 = dt.date(2026, 8, 6)          # biweekly = +14 days

LEGIT = {"Active", "Paid Leave", "Unpaid Leave"}
TERM = {"Terminated", "Retired"}

# Role drives the privileged flag -- a real export carries the role and you derive privilege
# from it, rather than shipping a separate hand-maintained "is privileged" column.
ROLES = ["Standard User"] * 14 + ["Read Only"] * 3 + ["Power User"] * 2 + ["Administrator"]
PRIVILEGED_ROLES = {"Administrator", "Service Account"}

DEPARTMENTS = ["Operations", "Finance", "IT", "Customer Service",
               "Fleet", "Sales", "Human Resources", "Security"]
TITLES = {
    "Operations": ["Operations Analyst", "Operations Supervisor", "Site Coordinator"],
    "Finance": ["Financial Analyst", "Accounts Payable Clerk", "Controller"],
    "IT": ["Systems Engineer", "Service Desk Analyst", "Platform Administrator"],
    "Customer Service": ["Customer Security Representative II CDL",
                         "Customer Care Associate", "Account Support Lead"],
    "Fleet": ["Fleet Maintenance Technician", "Route Driver", "Fleet Planner"],
    "Sales": ["Account Executive", "Sales Operations Analyst", "Regional Sales Manager"],
    "Human Resources": ["HR Business Partner", "Recruiter", "Benefits Analyst"],
    "Security": ["Security Analyst", "Physical Security Officer", "GRC Analyst"],
}

# Cycle-2 behaviours, all deliberate: what the second drop must prove.
REMEDIATION_RATE = 75      # % of cycle-1 findings actually removed from the app export
FALSE_CLAIM_COUNT = 2      # findings left in place -> task closed but account still there
NEW_TERM_COUNT = 8         # Active -> Terminated between cycles -> new findings
TRUNCATE_APP = "NA Saturn ComSat"   # export collapses -> trips the 50% sanity ratio
TRUNCATE_KEEP = 0.4
POST_TERM_LOGIN_COUNT = 3  # terminated accounts used AFTER the termination date


def hnum(key, mod):
    """Stable hash -> int, so every run of this generator produces identical files."""
    return int(hashlib.sha256(f"{SEED}|{key}".encode()).hexdigest(), 16) % mod


def serial_to_iso(value):
    """Excel serial -> ISO date. TalentHub uses serial 1 as its 'no date' sentinel."""
    s = str(value).strip()
    if not s.isdigit():
        return ""
    n = int(s)
    return "" if n <= 1 else (dt.date(1899, 12, 30) + dt.timedelta(days=n)).isoformat()


def name_from_upn(upn):
    """Canonical name rule (project CLAUDE.md): the sheets' name columns are de-id desynced
    for 835 rows, so first/last derive from the login local part instead."""
    parts = [p for p in upn.split("@")[0].replace("_", ".").split(".") if p]
    if not parts:
        return "", ""
    return parts[0].capitalize(), (parts[-1].capitalize() if len(parts) > 1 else "User")


def valid_upn(raw):
    """The 'Not found in TalentHub' sentinel leaks into TH_UPN itself -- reject it here."""
    upn = (raw or "").strip()
    if not upn or upn.lower() == NO_UPN.lower() or " " in upn or "@" not in upn:
        return ""
    return upn


# ------------------------------------------------------------------ source extraction

def read_stars():
    """Return {tab: [ {alias, upn, empid, status, hire, term} ]} straight off the workbook."""
    book = load_workbook_rows(SRC / "FAKE USERS - STARS Report.xlsx")
    out = {}
    for tab in STARS_TABS:
        rows = book[tab]
        cols = {v: k for k, v in rows[1].items()}
        alias_col = cols[[c for c in cols if c.endswith(("_NetworkAlias", "_USERNAME"))][0]]
        records = []
        for r in rows[2:]:
            if not any(str(v).strip() for v in r.values()):
                continue
            records.append({
                "alias": (r.get(alias_col) or "").strip(),
                "upn": valid_upn(r.get(cols["TH_UPN"])),
                "empid": (r.get(cols["TH_EmployeeID"]) or "").strip(),
                "status": (r.get(cols["TH_EmployeeStatus"]) or "").strip(),
                "hire": serial_to_iso(r.get(cols["TH_HireDate"])),
                "term": serial_to_iso(r.get(cols["TH_TerminationDate"])),
            })
        out[tab] = records
    return out


def build_hr(stars):
    """Collapse every app tab's embedded TH_* columns into one HR master keyed by UPN.

    Terminated wins on conflict: a person marked Terminated on any tab is terminated, and
    letting an Active row overwrite that would manufacture a false negative."""
    hr = {}
    for records in stars.values():
        for rec in records:
            if not rec["upn"]:
                continue
            key = rec["upn"].lower()
            if key in hr and hr[key]["employment_status"] in TERM:
                continue
            first, last = name_from_upn(rec["upn"])
            dept = DEPARTMENTS[hnum(f"dept|{key}", len(DEPARTMENTS))]
            hr[key] = {
                "employee_id": rec["empid"],
                "first_name": first,
                "last_name": last,
                "upn": rec["upn"],
                "business_email": rec["upn"],
                "employment_status": rec["status"],
                # Contractors are a real part of why accounts resist HR matching; naming them
                # converts part of the "not in TalentHub" pile into an answerable question.
                "worker_type": "Contractor" if hnum(f"wt|{key}", 100) < 9 else "Employee",
                "hire_date": rec["hire"],
                "termination_date": rec["term"],
                "job_title": TITLES[dept][hnum(f"title|{key}", len(TITLES[dept]))],
                "department": dept,
                "country": "Canada" if hnum(f"ctry|{key}", 100) < 15 else "United States",
                "manager_upn": "",
            }
    assign_managers(hr)
    return hr


def assign_managers(hr):
    """Populate manager_upn within each department.

    This column does not exist in today's process and is the enabler for manager-routed
    certification campaigns -- without it every review lands on one central reviewer."""
    by_dept = {}
    for key, row in hr.items():
        if row["employment_status"] in LEGIT:
            by_dept.setdefault(row["department"], []).append(key)
    managers = {}
    for dept, members in by_dept.items():
        members.sort()
        picked = [m for m in members if hnum(f"mgr|{m}", 12) == 0] or members[:1]
        managers[dept] = picked
    for key, row in hr.items():
        pool = managers.get(row["department"], [])
        if not pool:
            continue
        candidates = [m for m in pool if m != key]
        if candidates:
            row["manager_upn"] = hr[candidates[hnum(f"mgrpick|{key}", len(candidates))]]["upn"]


def build_app_rows(stars, hr, cycle_date):
    """App-native roster per app. Emits only what the application itself could know."""
    hr_keys = {k for k in hr}
    apps = {}
    for tab, records in stars.items():
        rows = []
        for rec in records:
            alias, upn = rec["alias"], rec["upn"]
            ident = f"{tab}|{alias}"
            if upn:
                email = upn                       # mixed case on purpose: the join must normalise
                display = " ".join(name_from_upn(upn))
            else:
                # No HR identity. Most such accounts still carry a plausible address; some
                # carry none at all. Never emit one that would accidentally match HR, or the
                # orphan would silently resolve.
                derived = f"{alias.lower()}@bitermtest.com" if alias else ""
                email = derived if derived and derived not in hr_keys and hnum(f"em|{ident}", 100) < 60 else ""
                display = alias.replace(".", " ").title() if alias else ""

            role = ("Service Account" if not upn and hnum(f"svc|{ident}", 100) < 35
                    else ROLES[hnum(f"role|{ident}", len(ROLES))])
            # Both must come from the HR master, not the row: the source workbook carries a
            # different (often blank) termination date per tab for the same person, and
            # mixing the two sources fabricates logins that look post-termination.
            hr_row = hr.get(upn.lower(), {})
            status = hr_row.get("employment_status", "")
            term_date = hr_row.get("termination_date", "")
            rows.append({
                "account_id": alias,
                "display_name": display,
                "email": email,
                # Terminated people mostly stay Enabled here -- that is the failure mode the
                # whole control exists to catch, so the mock must not quietly clean it up.
                "account_status": "Disabled" if hnum(f"as|{ident}", 100) < 6 else "Enabled",
                "app_role": role,
                "privileged": "Yes" if role in PRIVILEGED_ROLES else "No",
                "created_date": rec["hire"] or (cycle_date - dt.timedelta(days=900 + hnum(f"cd|{ident}", 800))).isoformat(),
                "last_login_date": last_login(ident, status, term_date, cycle_date),
            })
        apps[tab] = rows
    return apps


def last_login(ident, hr_status, term_date, cycle_date):
    """Activity column the real STARS export does not have.

    It is what makes orphan attribution possible at all, and it is how a terminated account
    that is still being USED becomes visible rather than merely present."""
    if hr_status in TERM and term_date:
        base = dt.date.fromisoformat(term_date)
        # A few logins land AFTER the termination date. That is not noise: it is the single
        # most alarming row type in the whole report, and it must exist to be demonstrable.
        offset = hnum(f"ptl|{ident}", 400)
        days = (offset % 25) + 1 if offset < POST_TERM_LOGIN_COUNT else -(hnum(f"ll|{ident}", 120) + 1)
        stamp = base + dt.timedelta(days=days)
        return min(stamp, cycle_date).isoformat()
    if not hr_status:                                   # orphan / service account
        age = hnum(f"orph|{ident}", 900) + 5
        return (cycle_date - dt.timedelta(days=age)).isoformat()
    return (cycle_date - dt.timedelta(days=hnum(f"act|{ident}", 45) + 1)).isoformat()


# ------------------------------------------------------------------ cycle 2 mutation

def findings(apps, hr):
    """Cycle-1 confirmed findings: joinable identity whose HR status is Terminated/Retired.

    Deduped by (app, account) to match biweekly_recon, which collapses duplicate roster rows
    into one finding -- francis.scott's three seats on one app are one removal, one ticket."""
    out = {}
    for tab, rows in apps.items():
        for row in rows:
            key = row["email"].lower()
            if key and hr.get(key, {}).get("employment_status") in TERM:
                out.setdefault((tab, row["account_id"]), row["email"])
    return sorted((tab, acct, email) for (tab, acct), email in out.items())


def mutate(stars, hr, apps, found):
    """Second drop: prove closure verification, false-claim detection and the sanity guard."""
    false_claims = [f for f in found[:FALSE_CLAIM_COUNT]]
    removable = found[FALSE_CLAIM_COUNT:]
    removed = [f for f in removable if hnum(f"rm|{f[0]}|{f[1]}", 100) < REMEDIATION_RATE]
    removed_keys = {(t, a) for t, a, _ in removed}

    hr2 = {k: dict(v) for k, v in hr.items()}
    active = sorted(k for k, v in hr2.items() if v["employment_status"] in LEGIT)
    new_terms = sorted(active, key=lambda k: hnum(f"newterm|{k}", 10 ** 9))[:NEW_TERM_COUNT]
    for key in new_terms:
        hr2[key]["employment_status"] = "Terminated"
        hr2[key]["termination_date"] = (
            CYCLE1 + dt.timedelta(days=1 + hnum(f"ntd|{key}", 12))).isoformat()

    apps2 = {}
    for tab, rows in build_app_rows(stars, hr2, CYCLE2).items():
        kept = [r for r in rows if (tab, r["account_id"]) not in removed_keys]
        if tab == TRUNCATE_APP:
            kept = kept[:int(len(kept) * TRUNCATE_KEEP)]
        apps2[tab] = kept
    return hr2, apps2, removed, false_claims, [hr2[k]["upn"] for k in new_terms]


# ------------------------------------------------------------------ writing

APP_COLS = ["account_id", "display_name", "email", "account_status",
            "app_role", "privileged", "created_date", "last_login_date"]
HR_COLS = ["employee_id", "first_name", "last_name", "upn", "business_email",
           "employment_status", "worker_type", "hire_date", "termination_date",
           "job_title", "department", "country", "manager_upn"]


def write_csv(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def slug(name):
    return name.replace(" ", "_")


def write_cycle(apps, hr, date):
    stamp = date.strftime("%Y%m%d")
    counts = {}
    for tab, rows in apps.items():
        counts[tab] = write_csv(OUT / tab / f"{slug(tab)}_users_{stamp}.csv", APP_COLS, rows)
    hr_rows = sorted(hr.values(), key=lambda r: r["upn"].lower())
    counts["_HR"] = write_csv(
        OUT / "_HR_TalentHub" / f"TalentHub_HR_{stamp}.csv", HR_COLS, hr_rows)
    return counts


def write_exceptions():
    """The exception list is reference data, not a biweekly drop -- it becomes a Workflows
    Table. Flattened to one CSV with an application column so it loads as a single table."""
    book = load_workbook_rows(SRC / "FAKE USERS - Exception List.xlsx")
    cols = ["application", "name", "upn", "employee_id", "app_account_alias",
            "exception_type", "justification", "owner_upn", "expiry"]
    rows = []
    for tab, sheet in book.items():
        for r in sheet[1:]:
            if not any(str(v).strip() for v in r.values()):
                continue
            rows.append(dict(zip(cols, [tab] + [(r.get(i) or "").strip() for i in range(8)])))
    return write_csv(OUT / "_reference" / "exception_list_20260723.csv", cols, rows)


def main():
    stars = read_stars()
    hr1 = build_hr(stars)
    apps1 = build_app_rows(stars, hr1, CYCLE1)
    found = findings(apps1, hr1)
    hr2, apps2, removed, false_claims, new_terms = mutate(stars, hr1, apps1, found)

    c1 = write_cycle(apps1, hr1, CYCLE1)
    c2 = write_cycle(apps2, hr2, CYCLE2)
    exceptions = write_exceptions()

    # Every showcase case counted and named individually. A total row count proves nothing
    # about whether a branch has data behind it.
    orphans = [(t, r["account_id"]) for t, rows in apps1.items() for r in rows
               if not r["email"] or r["email"].lower() not in hr1]
    unjoinable = [(t, r["account_id"]) for t, rows in apps1.items() for r in rows if not r["email"]]
    malformed = [v["upn"] for v in hr1.values()
                 if v["employment_status"] not in LEGIT | TERM]
    privileged_terms = [(t, a) for t, a, e in found
                        if any(r["account_id"] == a and r["privileged"] == "Yes" for r in apps1[t])]
    post_term = []
    for tab, acct, email in found:
        row = next(r for r in apps1[tab] if r["account_id"] == acct)
        term_date = hr1[email.lower()]["termination_date"]
        if row["last_login_date"] and term_date and row["last_login_date"] > term_date:
            post_term.append((tab, acct, term_date, row["last_login_date"]))
    contractors = [v["upn"] for v in hr1.values() if v["worker_type"] == "Contractor"]

    # Findings still present next cycle split into two DIFFERENT control outcomes: a closed
    # task whose account survived (false claim) versus a task nobody worked yet (aging).
    removed_keys = {(t, a) for t, a, _ in removed}
    false_keys = {(t, a) for t, a, _ in false_claims}
    aging = [(t, a) for t, a, _ in found if (t, a) not in removed_keys and (t, a) not in false_keys]

    manifest = {
        "generated": dt.date.today().isoformat(),
        "seed": SEED,
        "cycles": {CYCLE1.isoformat(): c1, CYCLE2.isoformat(): c2},
        "exception_rows": exceptions,
        "cases": {
            "confirmed_termination_with_access": {
                "count": len(found), "distinct_people": len({e for _, _, e in found}),
                "examples": found[:10]},
            "terminated_and_privileged": {"count": len(privileged_terms), "examples": privileged_terms[:10]},
            "login_after_termination_date": {"count": len(post_term), "examples": post_term},
            "orphan_not_in_hr": {"count": len(orphans), "examples": orphans[:10]},
            "app_row_unjoinable_blank_email": {"count": len(unjoinable), "examples": unjoinable[:10]},
            "hr_malformed_status": {"count": len(malformed), "examples": malformed[:10]},
            "contractor_worker_type": {"count": len(contractors), "examples": contractors[:5]},
            "cycle2_verified_closure": {"count": len(removed), "examples": [(t, a) for t, a, _ in removed[:10]]},
            "cycle2_false_claim_still_present": {"count": len(false_claims),
                                                 "examples": [(t, a) for t, a, _ in false_claims]},
            "cycle2_aging_not_yet_remediated": {"count": len(aging), "examples": aging[:10]},
            "cycle2_new_termination": {"count": len(new_terms), "examples": new_terms},
            "cycle2_export_anomaly": {"app": TRUNCATE_APP,
                                      "cycle1_rows": c1[TRUNCATE_APP], "cycle2_rows": c2[TRUNCATE_APP]},
        },
    }
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

    print(f"cycle {CYCLE1}: {sum(v for k, v in c1.items() if k != '_HR')} app rows / {c1['_HR']} HR rows")
    print(f"cycle {CYCLE2}: {sum(v for k, v in c2.items() if k != '_HR')} app rows / {c2['_HR']} HR rows")
    print(f"exception table rows: {exceptions}")
    for name, data in manifest["cases"].items():
        print(f"  {name}: {data.get('count', data)}")


if __name__ == "__main__":
    main()
