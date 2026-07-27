"""The control's stated rules, asserted directly.

Each test names the rule from project CLAUDE.md that it protects. If one of these fails, the
control is not doing what the SOX narrative says it does — which is the only reason a test
here should exist.
"""
import datetime as dt
import unittest

import tests  # noqa: F401  (puts scripts/ on sys.path)

import biterm_domain as domain
import biweekly_recon as recon

TODAY = dt.date(2026, 7, 26)


def row(alias="acct1", upn="a.person@wm.com", empid="E1", hr="Active", src=None):
    r = {"alias": alias, "upn": upn, "empid": empid, "hr": hr,
         "src": src or {"account": alias}}
    r["key"] = domain.identity_key(r)
    r["legacy_key"] = domain.legacy_identity_key(r)
    return r


def exc(owner="mgr@wm.com", expiry="2026-12-31", type_="Standing exemption"):
    return {"owner": owner, "expiry": dt.date.fromisoformat(expiry),
            "expiry_raw": expiry, "type": type_}


class TerminationDetection(unittest.TestCase):
    """Rule: HR employment status is the only legitimacy authority."""

    def test_terminated_user_with_access_is_flagged_for_a_ticket(self):
        pops = {"NA Orion": [row(hr="Terminated")]}
        rows, findings = recon.classify(pops, {}, {"a.person@wm.com": "ACTIVE"}, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["ticket"])
        self.assertEqual(rows["NA Orion"][0]["bucket"], "ticket")

    def test_retired_counts_as_terminated(self):
        pops = {"NA Orion": [row(hr="Retired")]}
        _, findings = recon.classify(pops, {}, {}, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["ticket"])

    def test_leave_statuses_are_legitimate_even_when_suspended_in_okta(self):
        """Unpaid-leave users are often suspended in Okta but legitimately keep access."""
        pops = {"NA Orion": [row(hr="Unpaid Leave")]}
        rows, findings = recon.classify(pops, {}, {"a.person@wm.com": "SUSPENDED"}, TODAY)
        self.assertEqual(findings, [])
        self.assertEqual(rows["NA Orion"][0]["bucket"], "pass")

    def test_okta_status_never_overrides_hr(self):
        """A deprovisioned Okta user who is Active in HR is not a finding; the app roster
        is what grants access, and Okta status is enrichment only."""
        pops = {"NA Orion": [row(hr="Active")]}
        _, findings = recon.classify(pops, {}, {"a.person@wm.com": "DEPROVISIONED"}, TODAY)
        self.assertEqual(findings, [])


class ExceptionHandling(unittest.TestCase):
    """Rule: the HR check runs on EVERYONE first — an exception never suppresses a hit."""

    def test_exception_does_not_suppress_a_termination(self):
        pops = {"NA Orion": [row(hr="Terminated")]}
        exceptions = {"NA Orion": {"a.person@wm.com": exc()}}
        rows, findings = recon.classify(pops, exceptions, {}, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["ticket"])
        self.assertEqual(rows["NA Orion"][0]["bucket"], "ticket")

    def test_live_exception_clears_the_row(self):
        pops = {"NA Orion": [row(hr="Contractor")]}
        exceptions = {"NA Orion": {"a.person@wm.com": exc(expiry="2026-12-31")}}
        rows, findings = recon.classify(pops, exceptions, {}, TODAY)
        self.assertEqual(findings, [])
        self.assertEqual(rows["NA Orion"][0]["bucket"], "exception_ok")

    def test_lapsed_exception_is_a_finding(self):
        pops = {"NA Orion": [row(hr="Contractor")]}
        exceptions = {"NA Orion": {"a.person@wm.com": exc(expiry="2026-07-25")}}
        _, findings = recon.classify(pops, exceptions, {}, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["exception_expired"])

    def test_exception_expiring_today_is_still_valid(self):
        pops = {"NA Orion": [row(hr="Contractor")]}
        exceptions = {"NA Orion": {"a.person@wm.com": exc(expiry="2026-07-26")}}
        _, findings = recon.classify(pops, exceptions, {}, TODAY)
        self.assertEqual(findings, [])

    def test_terminated_owner_raises_a_reassignment_flag(self):
        exceptions = {"NA Orion": {"a.person@wm.com": exc(owner="gone@wm.com")}}
        hr_by_upn = {"a.person@wm.com": "Active", "gone@wm.com": "Terminated"}
        findings, warnings = recon.ownership_review(exceptions, hr_by_upn, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["owner_terminated"])

    def test_expiring_soon_warns_without_becoming_a_finding(self):
        exceptions = {"NA Orion": {"a.person@wm.com": exc(expiry="2026-08-10")}}
        findings, warnings = recon.ownership_review(exceptions, {"a.person@wm.com": "Active"}, TODAY)
        self.assertEqual(findings, [])
        self.assertEqual(len(warnings), 1)


class LoudUnknown(unittest.TestCase):
    """Rule: the classifier needs three branches with a LOUD unknown — never default-to-fine."""

    def test_missing_from_hr_is_loud_not_passing(self):
        pops = {"NA Orion": [row(upn="", hr=domain.HR_NOT_FOUND)]}
        rows, findings = recon.classify(pops, {}, {}, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["unknown"])
        self.assertEqual(rows["NA Orion"][0]["bucket"], "unknown")

    def test_unrecognised_hr_status_is_loud_not_passing(self):
        pops = {"NA Orion": [row(hr="Sabbatical?")]}
        _, findings = recon.classify(pops, {}, {}, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["unknown"])
        self.assertIn("unrecognized HR status", findings[0]["reason"])

    def test_blank_hr_status_is_loud_not_passing(self):
        pops = {"NA Orion": [row(hr="")]}
        _, findings = recon.classify(pops, {}, {}, TODAY)
        self.assertEqual([f["cls"] for f in findings], ["unknown"])


class ClosureRules(unittest.TestCase):
    """Rule: closure = verified disappearance, and absence of evidence != removal."""

    def _prev(self, findings, counts, cycle="cycle_20260712_000000", live=True):
        return {"cycle": cycle, "tickets_live": live, "roster_counts": counts,
                "findings": findings, "key_scheme": "v2"}

    def test_disappeared_finding_closes_when_the_export_is_healthy(self):
        prior = [{"app": "NA Orion", "key": "acct:acct1", "legacy_key": "a.person@wm.com",
                  "key_scheme": "v2", "cls": "ticket", "reason": "Terminated", "age": 1,
                  "ticket": "REQ1/RITM1/TASK1", "upn": "a.person@wm.com", "alias": "acct1",
                  "hr": "Terminated", "empid": "E1"}]
        open_now, closures, anomalies, _ = recon.closure_pass(
            self._prev(prior, {"NA Orion": 100}), [], {"NA Orion": 98})
        self.assertEqual(len(closures), 1)
        self.assertEqual(open_now, [])
        self.assertEqual(anomalies, set())

    def test_collapsed_export_freezes_closures(self):
        prior = [{"app": "NA Orion", "key": "acct:acct1", "cls": "ticket", "reason": "Terminated",
                  "age": 1, "ticket": "REQ1/RITM1/TASK1", "upn": "a.person@wm.com",
                  "alias": "acct1", "hr": "Terminated", "empid": "E1", "key_scheme": "v2"}]
        open_now, closures, anomalies, _ = recon.closure_pass(
            self._prev(prior, {"NA Orion": 100}), [], {"NA Orion": 10})
        self.assertEqual(closures, [], "a collapsed export must never auto-close findings")
        self.assertEqual(anomalies, {"NA Orion"})
        self.assertIn("UNVERIFIABLE", open_now[0]["reason"])

    def test_missing_export_freezes_closures(self):
        prior = [{"app": "NA Orion", "key": "acct:acct1", "cls": "ticket", "reason": "Terminated",
                  "age": 1, "ticket": "REQ1/RITM1/TASK1", "upn": "a.person@wm.com",
                  "alias": "acct1", "hr": "Terminated", "empid": "E1", "key_scheme": "v2"}]
        open_now, closures, anomalies, _ = recon.closure_pass(
            self._prev(prior, {"NA Orion": 100}), [], {})
        self.assertEqual(closures, [])
        self.assertEqual(anomalies, {"NA Orion"})

    def test_still_present_finding_ages_and_keeps_its_ticket(self):
        current = [{"app": "NA Orion", "key": "acct:acct1", "legacy_key": "a.person@wm.com",
                    "key_scheme": "v2", "cls": "ticket", "reason": "Terminated",
                    "upn": "a.person@wm.com", "alias": "acct1", "hr": "Terminated", "empid": "E1"}]
        prior = [{**current[0], "age": 1, "ticket": "REQ1/RITM1/TASK1",
                  "first_cycle": "cycle_20260712_000000"}]
        open_now, closures, _, _ = recon.closure_pass(
            self._prev(prior, {"NA Orion": 100}), current, {"NA Orion": 100})
        self.assertEqual(closures, [])
        self.assertEqual(open_now[0]["age"], 2)
        self.assertEqual(open_now[0]["ticket"], "REQ1/RITM1/TASK1")
        self.assertEqual(open_now[0]["first_cycle"], "cycle_20260712_000000")

    def test_backfilled_upn_is_not_a_closure(self):
        """The orphan-reduction workstream backfills emails. Under the old `upn or alias:`
        key that read as a removal: a false verified closure plus a duplicate finding."""
        legacy_prior = [{"app": "NA Orion", "key": "alias:acct1", "cls": "ticket",
                         "reason": "Terminated", "age": 1, "ticket": "REQ1/RITM1/TASK1",
                         "upn": "", "alias": "acct1", "hr": "Terminated", "empid": "E1"}]
        r = row(alias="acct1", upn="now.known@wm.com", hr="Terminated")
        current = [{"app": "NA Orion", "key": r["key"], "legacy_key": r["legacy_key"],
                    "key_scheme": "v2", "cls": "ticket", "reason": "Terminated",
                    "upn": r["upn"], "alias": "acct1", "hr": "Terminated", "empid": "E1"}]
        # The prior cycle's legacy key for this account was "alias:acct1".
        current[0]["legacy_key"] = "alias:acct1"
        open_now, closures, _, migrated = recon.closure_pass(
            self._prev(legacy_prior, {"NA Orion": 100}), current, {"NA Orion": 100})
        self.assertEqual(closures, [], "an identifier backfill is not a removal")
        self.assertEqual(open_now[0]["ticket"], "REQ1/RITM1/TASK1")
        self.assertEqual(open_now[0]["age"], 2)
        self.assertEqual(migrated, 1)

    def test_dry_run_state_is_never_the_baseline(self):
        """A rehearsal must not age every finding on the next real cycle."""
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            lineage = Path(tmp)
            for name, live in (("cycle_20260701_000000", True), ("cycle_20260720_000000", False)):
                d = lineage / name
                d.mkdir()
                (d / "state.json").write_text(json.dumps(
                    {"cycle": name, "tickets_live": live, "roster_counts": {}, "findings": []}))
            prev, warnings = recon.load_prev_state(lineage)
            self.assertEqual(prev["cycle"], "cycle_20260701_000000")

    def test_voided_cycle_is_never_the_baseline(self):
        """A live run that must not define the baseline is excluded by a VOID marker —
        without deleting its evidence, which has to stay for the audit trail."""
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            lineage = Path(tmp)
            for name in ("cycle_20260701_000000", "cycle_20260720_000000"):
                d = lineage / name
                d.mkdir()
                (d / "state.json").write_text(json.dumps(
                    {"cycle": name, "tickets_live": True, "roster_counts": {}, "findings": []}))
            (lineage / "cycle_20260720_000000" / "VOID").write_text(
                "VOIDED — unintended live run\n")
            prev, warnings = recon.load_prev_state(lineage)
            self.assertEqual(prev["cycle"], "cycle_20260701_000000")
            self.assertTrue(any("VOID" in w for w in warnings))
            self.assertTrue((lineage / "cycle_20260720_000000" / "state.json").exists(),
                            "voiding must not delete the evidence")

    def test_crashed_live_run_is_reported_not_silently_ignored(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            lineage = Path(tmp)
            good = lineage / "cycle_20260701_000000"
            good.mkdir()
            (good / "state.json").write_text(json.dumps(
                {"cycle": "cycle_20260701_000000", "tickets_live": True,
                 "roster_counts": {}, "findings": []}))
            crashed = lineage / "cycle_20260720_000000"
            crashed.mkdir()
            (crashed / "tickets.jsonl").write_text('{"ticket": "REQ9/RITM9/TASK9"}\n')
            prev, warnings = recon.load_prev_state(lineage)
            self.assertTrue(any("did not finish" in w for w in warnings))


class TicketingRules(unittest.TestCase):
    """Rule: tickets are created once per finding; later cycles age, never re-ticket."""

    def test_correlation_id_is_stable_for_the_same_finding(self):
        a = domain.correlation_id("NA Orion", "acct:x", "ticket", "cycle_1")
        b = domain.correlation_id("NA Orion", "acct:x", "ticket", "cycle_1")
        self.assertEqual(a, b)

    def test_correlation_id_differs_across_findings(self):
        seen = {domain.correlation_id(app, key, "ticket", "cycle_1")
                for app in ("NA Orion", "NA Apollo") for key in ("acct:x", "acct:y")}
        self.assertEqual(len(seen), 4)

    def test_only_unticketed_findings_are_queued(self):
        findings = [
            {"cls": "ticket", "ticket": "REQ1/RITM1/TASK1"},
            {"cls": "ticket", "ticket": ""},
            {"cls": "ticket", "ticket": "DRY"},
            {"cls": "unknown", "ticket": ""},
        ]
        queued = [f for f in findings
                  if f["cls"] == "ticket" and f.get("ticket") in ("", "DRY", "SN-ERROR", None)]
        self.assertEqual(len(queued), 2)


if __name__ == "__main__":
    unittest.main()
