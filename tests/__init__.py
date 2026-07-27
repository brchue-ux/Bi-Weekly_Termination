"""Unit tests for the termination control.

The control's rules — HR is the only authority, an exception never suppresses a termination
hit, an unknown status is loud, absence of evidence is not removal — were previously
asserted only by running a full cycle against a live tenant and reading the output. They
are pure functions over plain dicts, so they are testable with no tenant, no credentials and
no network.

Run:  python3 tests/run_tests.py         (adds a mutation pass proving the suite can fail)
      python3 -m unittest discover -s tests -t .
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
