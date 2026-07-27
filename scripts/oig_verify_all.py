#!/usr/bin/env python3
"""Independent gate on the multi-app OIG load. Trusts nothing the loader REPORTED.

What "independent" honestly means here (corrected 2026-07-26): this verifier re-reads the
live tenant and re-derives the expected state from the source drops. It does NOT re-implement
the derivation — the privilege ordering, the email join and the aggregation live once in
`oig_common`, imported by both. They used to be copy-pasted into this file, which produced
two places to fix a bug and the *illusion* of a second opinion: any shared wrong assumption
passed both checks identically. Where genuine independence matters, the checks below rest on
facts the loader never computes: row/principal coverage arithmetic, and grants present for
principals absent from the drop.

For every app in oig_apps.json it checks:
  · app opted into EM
  · a `Role` entitlement exists with exactly that app's OWN distinct role values
  · every resolvable drop principal carries the HIGHEST-PRIVILEGE role among that person's rows
    (Administrator > Power User > Standard User > Read Only > Service Account) — this is the
    load's contract after the highest-wins rework, and the check that catches a hidden privilege
  · no principal carries a value while being absent from the drop
  · coverage reconciles: resolvable rows + orphan rows + unknown-role rows == every drop row

Duplicate rows are expected (multi-account users), so coverage is counted in ROWS while the grant
contract is checked per PRINCIPAL — the two must not be conflated (the old check added a
principal count to a row count and broke on every app with duplicates).

Bare/value-less grants (an app assignment with no entitlement value) cannot be deleted via the
API, so they are reported as a WARNING, not a failure — they carry no role for a reviewer to
certify. A value-less grant for someone absent from the drop is called out explicitly.

THREE-VALUED VERDICT (added 2026-07-26). This script previously had NO retry at all while the
loader retried 429/502/503, and swallowed paging errors with `break`. A rate limit could
therefore manufacture a verdict: a truncated read produced a spurious FAIL, or an under-read of
grants hid an `extra`. "I could not evaluate this" is now its own outcome:

    PASS          every check evaluated, every check passed
    FAIL          every check evaluated, at least one failed
    INCONCLUSIVE  at least one check could not be evaluated — exits non-zero, names the check

Run `oig_verify_all.py --selftest` to prove the checks actually fail on bad data. The selftest
corrupts EACH check in turn (it used to corrupt only the expected-role map, proving one check of
five) and asserts PER APP (it used to sum failures across the run, so one app emitting ten
failures while nine emitted none still "passed").

Usage: oig_verify_all.py [--only "NA Orion"] [--selftest] [--verbose]
"""
import argparse
import sys

import biterm_config
import okta_oauth
import biterm_creds
import biterm_domain as domain
import biterm_http
import biterm_runlog as runlog
import oig_common

log = None

# Every check this verifier makes, and the selftest corruption that must trip it. Keeping
# them in one table is what makes "prove each check is falsifiable" mechanical rather than
# aspirational.
CHECKS = ("em_enabled", "entitlement_present", "entitlement_values", "highest_privilege",
          "no_extra_grants", "row_coverage")


class Result:
    """Per-app outcome: which checks passed, failed, or could not be evaluated."""

    def __init__(self, tab):
        self.tab = tab
        self.failures = []       # (check, detail)
        self.inconclusive = []   # (check, detail)
        self.warnings = []
        self.stats = {}

    def check(self, name, ok, detail=""):
        if not ok:
            self.failures.append((name, detail))
        return ok

    def cannot_evaluate(self, name, detail):
        self.inconclusive.append((name, detail))

    @property
    def verdict(self):
        if self.inconclusive:
            return "INCONCLUSIVE"
        return "FAIL" if self.failures else "PASS"


def verify_app(client, app, emails, corrupt=None):
    """Verify one app. `corrupt` names a check to sabotage (selftest only)."""
    res = Result(app["tab"])

    try:
        ok, detail = oig_common.em_enabled(client, app)
    except biterm_http.HttpError as e:
        res.cannot_evaluate("em_enabled", str(e))
        return res
    if corrupt == "em_enabled":
        ok, detail = False, "SELFTEST"
    if not res.check("em_enabled", ok, detail):
        return res

    try:
        ent_id, valmap = oig_common.entitlement_values(client, app)
    except biterm_http.HttpError as e:
        res.cannot_evaluate("entitlement_present", str(e))
        return res
    if corrupt == "entitlement_present":
        ent_id = None
    if not res.check("entitlement_present", ent_id is not None,
                     "no `Role` entitlement on this app"):
        return res

    live_roles = sorted(valmap)
    if corrupt == "entitlement_values":
        live_roles = live_roles + ["__SELFTEST__"]
    res.check("entitlement_values", live_roles == sorted(app["roles"]),
              f"live={live_roles} expected={sorted(app['roles'])}")

    try:
        expected, stats = oig_common.expected_grants(app, emails)
    except (oig_common.DropError, domain.UnknownRoleError) as e:
        res.cannot_evaluate("highest_privilege", f"drop unusable: {e}")
        return res
    res.stats = stats

    if corrupt == "highest_privilege" and expected:
        expected = dict(expected)
        expected[next(iter(expected))] = "__SELFTEST__"

    id_to_name = {vid: name for name, vid in valmap.items()}
    try:
        granted, bare = oig_common.granted_values(client, app, id_to_name)
    except biterm_http.HttpError as e:
        # A truncated grants read cannot distinguish "not granted" from "not readable".
        res.cannot_evaluate("highest_privilege", f"grants not fully readable: {e}")
        res.cannot_evaluate("no_extra_grants", f"grants not fully readable: {e}")
        return res

    if corrupt == "no_extra_grants":
        granted = {**granted, "__SELFTEST_PRINCIPAL__": ["Administrator"]}

    mism = [(u, e, granted.get(u)) for u, e in expected.items() if granted.get(u) != [e]]
    res.check("highest_privilege", not mism,
              f"{len(mism)} mismatched, e.g. {mism[:2]}")
    extra = set(granted) - set(expected)
    res.check("no_extra_grants", not extra, f"{len(extra)} principal(s) hold a value but are "
                                            f"absent from the drop: {sorted(extra)[:5]}")

    counted = stats["resolvable_rows"] + stats["orphan_rows"] + stats["unknown_role_rows"]
    if corrupt == "row_coverage":
        counted += 1
    res.check("row_coverage", counted == stats["rows"],
              f"{stats['resolvable_rows']}+{stats['orphan_rows']}+{stats['unknown_role_rows']} "
              f"= {counted} vs {stats['rows']} rows")

    if bare:
        res.warnings.append(f"bare_grants={bare} (value-less; cannot be deleted via API)")
    if stats["unknown_role_rows"]:
        res.warnings.append(f"unknown_role_rows={stats['unknown_role_rows']} "
                            f"{stats['unknown_roles']} — those rows carry no certifiable role")
    return res


def report(results):
    for r in results:
        s = r.stats
        marker = {"PASS": "ok  ", "FAIL": "FAIL", "INCONCLUSIVE": "????"}[r.verdict]
        warn = ("  WARN " + "; ".join(r.warnings)) if r.warnings else ""
        log.info(f"  {marker} {r.tab:<20} principals={s.get('principals', '?'):>4} "
                 f"orphan_rows={s.get('orphan_rows', '?'):>4} rows={s.get('rows', '?'):>4}{warn}")
    for r in results:
        for name, detail in r.failures:
            log.error(f"FAIL [{r.tab}] {name}{' — ' + detail if detail else ''}")
        for name, detail in r.inconclusive:
            log.error(f"INCONCLUSIVE [{r.tab}] {name} — {detail}")


def selftest(client, manifest, emails):
    """Prove EVERY check is falsifiable, per app, one corruption at a time.

    The old selftest corrupted a single map and asserted `len(failures) >= len(manifest)` —
    a run-wide sum, so one app producing ten failures while nine produced none still passed.
    """
    broken = []
    for check in CHECKS:
        survived = []
        for app in manifest:
            res = verify_app(client, app, emails, corrupt=check)
            # The named check must appear in THIS app's failures. Asserting per app is the
            # point: a run-wide failure count can be satisfied entirely by one noisy app.
            if not any(name == check for name, _ in res.failures):
                survived.append(f"{check} did NOT fail on corrupted data for {app['tab']}"
                                + (f" (inconclusive: {res.inconclusive})" if res.inconclusive else ""))
        broken += survived
        log.info(f"  selftest {check:<22} "
                 + ("ok — trips on every app" if not survived
                    else f"BROKEN on {len(survived)}/{len(manifest)} app(s)"))
    if broken:
        log.error("\nSELFTEST BROKEN — these checks cannot detect the fault they exist for:")
        for b in broken:
            log.error(f"    · {b}")
        return 2
    log.info(f"\nSELFTEST: OK — all {len(CHECKS)} checks fail on corrupted data, "
             f"on all {len(manifest)} app(s)")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", metavar="TAB", help="limit to one app tab from oig_apps.json")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every check fails on corrupted data, then exit")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    global log
    args = parse_args(argv)
    log = runlog.setup("oig_verify_all", verbose=args.verbose)
    log.info(f"run {runlog.run_id()} | {biterm_config.describe()}")

    client = oig_common.admin_client("oig_verify_all", dry_run=True, logger=log)
    manifest = oig_common.load_manifest(args.only)
    try:
        emails = oig_common.users_by_email(client)
    except (biterm_http.HttpError, oig_common.DuplicateIdentityError) as e:
        log.error(f"INCONCLUSIVE — could not build the identity map: {e}")
        log.error("\nVERDICT: INCONCLUSIVE (0 apps evaluated)")
        return 2

    if args.selftest:
        return selftest(client, manifest, emails)

    results = [verify_app(client, app, emails) for app in manifest]
    report(results)

    n_fail = sum(1 for r in results if r.verdict == "FAIL")
    n_inc = sum(1 for r in results if r.verdict == "INCONCLUSIVE")
    checks_failed = sum(len(r.failures) for r in results)
    if n_inc:
        verdict, code = "INCONCLUSIVE", 2
    elif n_fail:
        verdict, code = "FAIL", 1
    else:
        verdict, code = "PASS", 0
    log.info(f"\nVERDICT: {verdict} ({len(manifest)} apps, {checks_failed} failed checks, "
             f"{n_inc} app(s) not evaluable)")
    if verdict == "PASS":
        log.info("Run --selftest to confirm these checks can still fail.")
    return code


if __name__ == "__main__":
    # Entrypoints translate typed library errors into a clean exit. Library code
    # never calls sys.exit itself — the caller decides what is fatal.
    try:
        sys.exit(main())
    except (biterm_config.ConfigError, biterm_creds.CredentialError,
            biterm_http.HttpError, okta_oauth.OAuthError,
            oig_common.ManifestError, oig_common.DropError) as e:
        sys.exit(f"ABORTED: {e}")
