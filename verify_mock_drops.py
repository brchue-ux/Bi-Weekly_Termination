#!/usr/bin/env python3
"""Independent gate on the mock drop files.

Reads ONLY the written CSVs back off disk and re-derives every claim from scratch -- it
never imports the generator's in-memory state, because a generator agreeing with itself
proves nothing. Ends in a single VERDICT line.

Checks:
  1  structure      one folder per app, both cycles present, HR + exception table present
  2  no-HR-columns  app exports must not carry employment status (the architectural point)
  3  joinability    app email -> HR upn resolves case-insensitively
  4  cases          every showcase branch has real rows behind it, named individually
  5  cycle-2 proof  closures absent, false claims present, export anomaly trips 50% ratio
  6  tenant         a sample of flagged identities resolves live in Okta
"""
import csv
import json
import sys
from pathlib import Path

PROJ = Path(__file__).parent
OUT = PROJ / "bi-weekly term and app list"
STAMP1, STAMP2 = "20260723", "20260806"
LEGIT = {"Active", "Paid Leave", "Unpaid Leave"}
TERM = {"Terminated", "Retired"}
SANITY_RATIO = 0.5     # biweekly_recon.ROSTER_SANITY_RATIO
HR_LEAK_COLS = {"employment_status", "hr_status", "th_employeestatus",
                "termination_date", "employee_id", "manager_upn"}

failures, notes = [], []


def check(label, ok, detail=""):
    (notes if ok else failures).append(f"{'ok  ' if ok else 'FAIL'} {label}{' — ' + detail if detail else ''}")
    return ok


def read(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def app_dirs():
    return sorted(d for d in OUT.iterdir() if d.is_dir() and not d.name.startswith("_"))


def main():
    if not OUT.exists():
        print("FAIL no drop folder"), sys.exit(1)

    # 1 ---------------------------------------------------------------- structure
    apps = app_dirs()
    check("structure: 10 app folders", len(apps) == 10, f"found {len(apps)}")
    missing = [d.name for d in apps
               if not (d / f"{d.name.replace(' ', '_')}_users_{STAMP1}.csv").exists()
               or not (d / f"{d.name.replace(' ', '_')}_users_{STAMP2}.csv").exists()]
    check("structure: both cycles in every app folder", not missing, str(missing))
    hr1_path = OUT / "_HR_TalentHub" / f"TalentHub_HR_{STAMP1}.csv"
    hr2_path = OUT / "_HR_TalentHub" / f"TalentHub_HR_{STAMP2}.csv"
    exc_path = OUT / "_reference" / "exception_list_20260723.csv"
    check("structure: HR + exception files", all(p.exists() for p in (hr1_path, hr2_path, exc_path)))

    c1 = {d.name: read(d / f"{d.name.replace(' ', '_')}_users_{STAMP1}.csv") for d in apps}
    c2 = {d.name: read(d / f"{d.name.replace(' ', '_')}_users_{STAMP2}.csv") for d in apps}
    hr1 = {r["upn"].lower(): r for r in read(hr1_path)}
    hr2 = {r["upn"].lower(): r for r in read(hr2_path)}
    exceptions = read(exc_path)

    # 2 ---------------------------------------------------------------- no HR leakage
    leaked = set()
    for rows in c1.values():
        if rows:
            leaked |= {c for c in rows[0] if c.lower() in HR_LEAK_COLS}
    check("app exports carry no HR columns", not leaked, str(sorted(leaked)))
    check("HR export carries employment_status", "employment_status" in next(iter(hr1.values())))

    # 3 ---------------------------------------------------------------- joinability
    total = sum(len(r) for r in c1.values())
    joined = sum(1 for rows in c1.values() for r in rows if r["email"].lower() in hr1)
    check("join resolves for the majority of app rows", joined > total * 0.8,
          f"{joined}/{total} joined")
    mixed_case = sum(1 for rows in c1.values() for r in rows if r["email"] != r["email"].lower())
    check("join is case-normalising (mixed-case emails present)", mixed_case > 0,
          f"{mixed_case} mixed-case emails")

    # 4 ---------------------------------------------------------------- showcase cases
    found = sorted((tab, r["account_id"], r["email"].lower())
                   for tab, rows in c1.items() for r in rows
                   if r["email"].lower() in hr1
                   and hr1[r["email"].lower()]["employment_status"] in TERM)
    people = {e for _, _, e in found}
    accounts = {(t, a) for t, a, _ in found}
    # Three different units, all real: raw roster rows, distinct accounts (what biweekly_recon
    # collapses duplicate rows into, = one ticket each), and distinct humans. Reporting only
    # one of them is how the manifest and this gate would appear to disagree.
    check("case: confirmed terminations with access", len(found) > 0,
          f"{len(found)} roster rows = {len(accounts)} accounts across {len(people)} people")

    privileged = [(t, a) for t, a, _ in found
                  if any(r["account_id"] == a and r["privileged"] == "Yes" for r in c1[t])]
    check("case: terminated AND privileged", len(privileged) > 0, str(privileged[:3]))

    post_term = [(t, a, hr1[e]["termination_date"],
                  next(r for r in c1[t] if r["account_id"] == a)["last_login_date"])
                 for t, a, e in found
                 if (lambda r: r["last_login_date"] and hr1[e]["termination_date"]
                     and r["last_login_date"] > hr1[e]["termination_date"])(
                         next(r for r in c1[t] if r["account_id"] == a))]
    check("case: login AFTER termination date", len(post_term) > 0, str(post_term[:3]))

    orphans = [(t, r["account_id"]) for t, rows in c1.items() for r in rows
               if r["email"].lower() not in hr1]
    check("case: orphans absent from HR", len(orphans) > 100, f"{len(orphans)} rows")

    malformed = [r["upn"] for r in hr1.values()
                 if r["employment_status"] not in LEGIT | TERM]
    check("case: malformed HR status (loud unknown)", len(malformed) > 0,
          f"{len(malformed)}, e.g. {malformed[:2]}")

    contractors = [r for r in hr1.values() if r["worker_type"] == "Contractor"]
    check("case: contractor worker_type present", len(contractors) > 0, f"{len(contractors)}")

    managed = [r for r in hr1.values() if r["manager_upn"]]
    check("case: manager_upn populated (campaign routing)", len(managed) > len(hr1) * 0.8,
          f"{len(managed)}/{len(hr1)}")
    self_managed = [r["upn"] for r in hr1.values() if r["manager_upn"].lower() == r["upn"].lower()]
    check("manager_upn never self-referential", not self_managed, str(self_managed[:3]))

    exc_expired = [r for r in exceptions if r["expiry"] and r["expiry"] < "2026-07-23"]
    check("case: expired exceptions", len(exc_expired) > 0, f"{len(exc_expired)}")
    exc_dead_owner = [r for r in exceptions
                      if r["owner_upn"].lower() in hr1
                      and hr1[r["owner_upn"].lower()]["employment_status"] in TERM]
    check("case: exception owner terminated", len(exc_dead_owner) > 0, f"{len(exc_dead_owner)}")

    # 5 ---------------------------------------------------------------- cycle-2 proof
    c2_keys = {(t, r["account_id"]) for t, rows in c2.items() for r in rows}
    c1_finding_keys = {(t, a) for t, a, _ in found}
    closed = c1_finding_keys - c2_keys
    still = c1_finding_keys & c2_keys
    check("cycle 2: verified closures (rows genuinely gone)", len(closed) > 0, f"{len(closed)}")

    # A survivor is only a FALSE CLAIM if a task was closed against it; otherwise it is simply
    # unworked and ages. Conflating the two would let the demo claim a detection it never made,
    # so the named false-claim accounts are asserted individually against the manifest.
    manifest = json.loads((OUT / "MANIFEST.json").read_text())
    named_false = {tuple(x) for x in manifest["cases"]["cycle2_false_claim_still_present"]["examples"]}
    check("cycle 2: every NAMED false claim survived into the next export",
          named_false and named_false <= still, f"{sorted(named_false)}")
    check("cycle 2: aging population is the remainder, counted separately",
          len(still - named_false) == manifest["cases"]["cycle2_aging_not_yet_remediated"]["count"],
          f"{len(still - named_false)} aging vs manifest "
          f"{manifest['cases']['cycle2_aging_not_yet_remediated']['count']}")

    new_terms = [u for u, r in hr2.items()
                 if r["employment_status"] in TERM
                 and hr1.get(u, {}).get("employment_status") in LEGIT]
    check("cycle 2: new terminations appear in HR", len(new_terms) > 0, f"{len(new_terms)}")

    anomaly = [(t, len(c1[t]), len(c2[t])) for t in c1
               if len(c1[t]) and len(c2[t]) / len(c1[t]) < SANITY_RATIO]
    check("cycle 2: an export trips the 50% sanity ratio", len(anomaly) == 1, str(anomaly))
    shrink_ok = [t for t in c1 if t not in {a[0] for a in anomaly}
                 and len(c1[t]) and len(c2[t]) / len(c1[t]) < SANITY_RATIO]
    check("cycle 2: no OTHER export collapses accidentally", not shrink_ok, str(shrink_ok))

    # 6 ---------------------------------------------------------------- live tenant
    # A demo whose files name people who do not exist in the tenant is worse than no demo,
    # so this resolves real logins rather than trusting that the rename kept them aligned.
    # okta_client.api raises SystemExit (a BaseException) on HTTP failure — catching only
    # Exception here would turn a dead credential into a silent skip.
    # Every flagged identity must EITHER resolve live in Okta OR be one the seeding
    # deliberately never created (the "no Okta account" branch of the 3-way join). Asserting
    # that all of them resolve would be wrong -- absence is a designed test surface here --
    # but so would ignoring absence, which is why each one is attributed to a reason.
    try:
        from okta_client import api
        seeded = set(json.loads((PROJ / "seed_manifest.json").read_text())["users"])
        live, absent, unexplained = [], [], []
        for upn in sorted(people):
            hit, _ = api("GET", f"/api/v1/users/{upn}", ok404=True)
            if hit and hit.get("profile", {}).get("login", "").lower() == upn:
                live.append(upn)
            elif upn in seeded:
                unexplained.append(upn)     # manifest says created, tenant disagrees
            else:
                absent.append(upn)
        check("tenant: every flagged identity resolves live or is deliberately unseeded",
              not unexplained, f"unexplained: {unexplained}")
        check("tenant: flagged identities resolve live", len(live) > 0,
              f"{len(live)}/{len(people)} live")
        check("tenant: 'no Okta account' branch has real data", len(absent) > 0,
              f"{len(absent)} never seeded, e.g. {absent[:3]}")
    except BaseException as exc:            # okta_client raises SystemExit, not Exception
        check("tenant: flagged identities checked against live Okta", False,
              f"could not verify — {type(exc).__name__}: {str(exc)[:200]}")

    # ---------------------------------------------------------------- verdict
    for line in notes:
        print(line)
    for line in failures:
        print(line)
    print(f"\nVERDICT: {'PASS' if not failures else 'FAIL'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
