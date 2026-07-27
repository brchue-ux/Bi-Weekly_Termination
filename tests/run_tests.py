#!/usr/bin/env python3
"""Run the suite, then PROVE it can fail.

A green test suite is evidence only if a broken control turns it red. This runner does two
passes:

  1. the normal suite — every control rule and safety guard;
  2. a MUTATION pass — each of the control's load-bearing rules is deliberately broken, one
     at a time, and the suite must go red for that specific rule. A mutation the suite
     survives is a rule nobody is actually testing, and it is reported by name.

This is the same discipline as `oig_verify_all.py --selftest`, applied to the tests
themselves: prove the checker is falsifiable before quoting its verdict.

Usage: python3 tests/run_tests.py [-v]
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tests  # noqa: E402,F401

TEST_DIR = Path(__file__).resolve().parent


def load_suite():
    loader = unittest.TestLoader()
    return loader.discover(start_dir=str(TEST_DIR), top_level_dir=str(TEST_DIR.parent))


def run(verbosity=1):
    runner = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stderr)
    return runner.run(load_suite())


# Each mutation breaks ONE stated rule. The comment is the rule it breaks.
def _mutations():
    import biterm_domain as domain
    import biweekly_recon as recon
    import run_all

    def set_attr(module, name, value):
        original = getattr(module, name)

        def apply():
            setattr(module, name, value)

        def undo():
            setattr(module, name, original)
        return apply, undo

    return {
        # "Terminated/Retired = flag" — the control's whole purpose.
        "termination_detection": set_attr(recon, "TERM", frozenset()),
        # "Paid/Unpaid leave is legitimate access" — over-flagging is also a defect.
        "leave_is_legitimate": set_attr(recon, "LEGIT", frozenset({"Active"})),
        # "A collapsed export never auto-closes findings."
        "export_anomaly_freeze": set_attr(recon, "ROSTER_SANITY_RATIO", 0.0),
        # "An expiry is a real date, not a string."
        "expiry_is_a_date": set_attr(
            domain, "is_expired", lambda expiry, today: False),
        # "Privilege can never be hidden behind a lower role."
        "highest_privilege_wins": set_attr(
            domain, "PRIVILEGE_ORDER", {k: 0 for k in domain.PRIVILEGE_ORDER}),
        # "A finding's identity is stable across an identifier backfill."
        "stable_identity_key": set_attr(
            domain, "identity_key", domain.legacy_identity_key),
        # "Refuse an implausibly large removal set."
        "removal_blast_radius": set_attr(
            run_all, "guard_removals", lambda *a, **k: None),
    }


def mutation_pass(verbosity=0):
    """Return a list of mutations the suite FAILED to detect."""
    survived = []
    for name, (apply_mutation, undo) in _mutations().items():
        apply_mutation()
        try:
            result = unittest.TextTestRunner(
                verbosity=verbosity, stream=open("/dev/null", "w")).run(load_suite())
            broke_something = not result.wasSuccessful()
        finally:
            undo()
        status = "detected" if broke_something else "SURVIVED"
        print(f"  mutation {name:<26} {status}", file=sys.stderr)
        if not broke_something:
            survived.append(name)
    return survived


def main():
    verbosity = 2 if "-v" in sys.argv else 1
    print("=== suite ===", file=sys.stderr)
    result = run(verbosity)
    if not result.wasSuccessful():
        print("\nVERDICT: FAIL — the suite is red; fix that before trusting the mutation pass.",
              file=sys.stderr)
        return 1

    print("\n=== mutation pass (each breaks one control rule; the suite must go red) ===",
          file=sys.stderr)
    survived = mutation_pass()
    if survived:
        print(f"\nVERDICT: FAIL — {len(survived)} mutation(s) SURVIVED, meaning these rules "
              f"are not actually covered:", file=sys.stderr)
        for s in survived:
            print(f"    · {s}", file=sys.stderr)
        return 2

    print(f"\nVERDICT: PASS — {result.testsRun} tests green, and all "
          f"{len(_mutations())} control-rule mutations were caught.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
