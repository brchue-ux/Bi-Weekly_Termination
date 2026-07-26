#!/usr/bin/env python3
"""Independent verification gate for the tenant seed (project CLAUDE.md mandate).

Recomputes expected state from the SOURCE xlsx files via seed_tenant.build_plan()
and reconciles it against FRESH Okta API pulls. seed_manifest.json is never the
source of truth — it is the artifact under audit (cross-checked as INFO only).
Ends in a single `VERDICT: PASS|FAIL` line; exits nonzero on any FAIL.

Canonical profile-name rule (regardless of what build_plan carries — the SFDC
name columns are de-id scrambled, see CLAUDE.md): tokens = login local part with
"_"→".", split on "."; firstName = tokens[0].title(); lastName = tokens[-1].title()
if more than one token else "User".

Usage: verify_seed.py [--app LABEL]   # LABEL restricts check 4 (assignments)
                                      # to one app; all other checks run fully.
"""
import argparse
import sys
import urllib.parse

# api()/paged() give 429-backoff + link-header pagination; importing runs no network.
from seed_tenant import build_plan, paged, MANIFEST

DOMAIN = "@bitermtest.com"   # every seeded identity lives here; nothing else is ours
BASELINE_OTHERS = 18         # pre-existing demo-org users counted before seeding
EXAMPLES = 10                # max concrete failing examples printed per check


def canonical(login: str):
    """The one true expected-name rule, derived from the login local part."""
    tokens = login.split("@")[0].replace("_", ".").split(".")
    return tokens[0].title(), (tokens[-1].title() if len(tokens) > 1 else "User")


def check(name: str, failures: list, context: str = "") -> bool:
    """Print one check's PASS/FAIL line + up to EXAMPLES failing cases; return ok."""
    ok = not failures
    tail = f" ({context})" if context else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {len(failures)} failing{tail}")
    for f in failures[:EXAMPLES]:
        print(f"    - {f}")
    if len(failures) > EXAMPLES:
        print(f"    ... and {len(failures) - EXAMPLES} more")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", help="restrict check 4 to this one app label (smoke test)")
    args = ap.parse_args()

    users, apps, _orphans = build_plan()
    if args.app and args.app not in apps:
        sys.exit(f"--app {args.app!r} is not a planned label; choices: {sorted(apps)}")
    planned_present = {l: u for l, u in users.items() if u["fate"] != "absent"}
    planned_absent = sorted(l for l, u in users.items() if u["fate"] == "absent")

    # -------- fresh pulls (the only evidence). Default listing hides DEPROVISIONED
    # users, so one extra filtered pull keeps "absent"/"untouched" honest.
    print("Fetching live users (~38 pages) ...", file=sys.stderr)
    live = list(paged("/api/v1/users?limit=200"))
    dep_q = urllib.parse.urlencode({"limit": 200, "filter": 'status eq "DEPROVISIONED"'})
    live += list(paged(f"/api/v1/users?{dep_q}"))
    by_login = {u["profile"]["login"].lower(): u for u in live}
    uid_to_login = {u["id"]: u["profile"]["login"].lower() for u in live}
    print(f"Live pull: {len(by_login)} users", file=sys.stderr)

    results = {}

    # -------- 1 USERS-EXIST: planned present exist, planned absent don't,
    # and no @bitermtest.com stray exists outside the plan.
    fails = [f"missing: {l} (fate {planned_present[l]['fate']})"
             for l in sorted(planned_present) if l not in by_login]
    fails += [f"should-be-absent but exists: {l} (status {by_login[l]['status']})"
              for l in planned_absent if l in by_login]
    fails += [f"unplanned {DOMAIN} user: {l}"
              for l in sorted(by_login) if l.endswith(DOMAIN) and l not in users]
    results["1 USERS-EXIST"] = check(
        "1 USERS-EXIST", fails,
        f"{len(planned_present)} planned present, {len(planned_absent)} planned absent")

    # -------- 2 STATUS: fate active -> ACTIVE, fate suspended -> SUSPENDED.
    # Anything else (PROVISIONED, PASSWORD_RESET, ...) is a failure by rule.
    want_status = {"active": "ACTIVE", "suspended": "SUSPENDED"}
    fails = [f"{l}: fate {u['fate']} but status {by_login[l]['status']}"
             for l, u in sorted(planned_present.items())
             if l in by_login and by_login[l]["status"] != want_status[u["fate"]]]
    results["2 STATUS"] = check("2 STATUS", fails, "missing users counted in check 1")

    # -------- 3 NAMES: every live @bitermtest.com profile matches the canonical rule.
    fails = []
    for l in sorted(by_login):
        if not l.endswith(DOMAIN):
            continue
        first, last = canonical(l)
        p = by_login[l]["profile"]
        if (p.get("firstName"), p.get("lastName")) != (first, last):
            fails.append(f"{l}: has ({p.get('firstName')!r}, {p.get('lastName')!r}), "
                         f"want ({first!r}, {last!r})")
    results["3 NAMES"] = check("3 NAMES", fails, "fixer may still be running")

    # -------- 4 ASSIGNMENTS: per app, assigned logins == unique non-absent roster logins.
    live_apps = {a["label"]: a["id"] for a in paged("/api/v1/apps?limit=200")}
    labels = [args.app] if args.app else sorted(apps)
    fails = []
    for label in labels:
        expected = {l for l in apps[label] if users[l]["fate"] != "absent"}
        if label not in live_apps:
            fails.append(f"{label}: app not found in tenant")
            continue
        actual = set()
        for au in paged(f"/api/v1/apps/{live_apps[label]}/users?limit=200"):
            actual.add(uid_to_login.get(au["id"], f"<unknown uid {au['id']}>"))
        missing, extra = expected - actual, actual - expected
        if missing or extra:
            fails.append(f"{label}: {len(missing)} missing (e.g. {sorted(missing)[:3]}), "
                         f"{len(extra)} extra (e.g. {sorted(extra)[:3]})")
        else:
            print(f"    ok {label}: {len(actual)} assigned == {len(expected)} expected")
    scope = f"1 app (--app smoke), {len(apps) - 1} apps NOT verified" if args.app \
        else f"all {len(labels)} apps"
    results["4 ASSIGNMENTS"] = check("4 ASSIGNMENTS", fails, scope)

    # -------- 5 UNTOUCHED: pre-existing (non-bitermtest) users all still present.
    others = {l: u for l, u in by_login.items() if not l.endswith(DOMAIN)}
    for l in sorted(others):
        print(f"    pre-existing: {l} [{others[l]['status']}]")
    warns = [f"{l}: status {u['status']}" for l, u in sorted(others.items())
             if u["status"] in ("SUSPENDED", "DEPROVISIONED")]
    for w in warns:
        print(f"    WARN (prior status unknown, not a FAIL): {w}")
    fails = ([f"only {len(others)} non-{DOMAIN} users, expected >= {BASELINE_OTHERS}"]
             if len(others) < BASELINE_OTHERS else [])
    results["5 UNTOUCHED"] = check("5 UNTOUCHED", fails,
                                   f"{len(others)} pre-existing, {len(warns)} warns")

    # -------- secondary: the seeder's own manifest, reported but never trusted.
    if MANIFEST.exists():
        import json
        m = json.loads(MANIFEST.read_text())
        print(f"INFO manifest (secondary report, not evidence): records "
              f"{len(m.get('users', {}))} users / {len(m.get('apps', {}))} apps; "
              f"plan expects {len(planned_present)} users / {len(apps)} apps")

    # -------- summary + single verdict line.
    print("\n===== SUMMARY =====")
    for name, ok in results.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    verdict = all(results.values())
    print(f"VERDICT: {'PASS' if verdict else 'FAIL'}")
    sys.exit(0 if verdict else 1)


if __name__ == "__main__":
    main()
