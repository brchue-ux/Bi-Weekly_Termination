"""Run logging and change evidence.

There was no `import logging` anywhere in this repo. Everything was `print()` with no run
id, timestamp, or level — and the mutating scripts (entitlement loader, SAML rollout,
seeder) left no artifact at all of what they changed. The read-only verifier produced better
evidence than the writers did, which is backwards for a SOX control.

Two outputs:
  * a human log to stderr (unchanged operator experience — the existing progress lines keep
    working) and to `logs/<run_id>.log`;
  * a machine-readable change log, `logs/<run_id>.changes.jsonl`, one line per mutating API
    call: {ts, run_id, actor, method, url, status, request, response}. That file is what an
    auditor is actually asking for, and it makes a silent partial write visible after the
    fact instead of scrolling off a terminal.

`evidence_manifest()` writes SHA256 digests of a cycle's inputs and outputs so a report can
be proven to match the data it was produced from. Without it, "regenerable from the scripts"
is not true: the outputs also depend on live tenant state at run time.
"""
import getpass
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

import biterm_domain

PROJ = Path(__file__).resolve().parent.parent
LOG_DIR = PROJ / "logs"

_run_id = None
_change_file = None


def run_id():
    global _run_id
    if _run_id is None:
        _run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}"
    return _run_id


def actor():
    """Who ran this. Recorded on every change so a write is always attributable."""
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:
        return "unknown"


def setup(name, verbose=False, to_file=True):
    """Configure logging for a script. Returns a logger; safe to call once per process."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stream)
    if to_file:
        LOG_DIR.mkdir(exist_ok=True)
        fh = logging.FileHandler(LOG_DIR / f"{run_id()}.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s %(message)s"))
        logger.addHandler(fh)
    logger.propagate = False
    return logger


def change_recorder(script, dry_run=False):
    """Return an `on_write` callback for biterm_http.Client.

    In dry-run mode nothing should reach it; if something does, it is still recorded (marked
    `dry_run: true`) because an unexpected write during a rehearsal is itself a finding.
    """
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"{run_id()}.changes.jsonl"

    def record(entry):
        global _change_file
        if _change_file is None:
            _change_file = open(path, "a", encoding="utf-8")
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": run_id(), "script": script, "actor": actor(), "dry_run": dry_run,
            **entry,
        }
        _change_file.write(json.dumps(line, default=str) + "\n")
        _change_file.flush()          # a crash must not lose the record of what was written
        os.fsync(_change_file.fileno())
    return record


def change_log_path():
    return LOG_DIR / f"{run_id()}.changes.jsonl"


def evidence_manifest(out_dir, inputs, outputs, extra=None):
    """Write SHA256SUMS-style integrity evidence for one cycle.

    `inputs` proves which files were reviewed; `outputs` proves the report has not been
    edited since. Together they make the cycle reproducible from its stated inputs, which
    the pipeline previously could not claim (the exception register, for one, was selected
    by "newest file on disk" rather than by cycle date).
    """
    out_dir = Path(out_dir)
    lines, records = [], {"inputs": {}, "outputs": {}}
    for label, group in (("inputs", inputs), ("outputs", outputs)):
        for p in group:
            p = Path(p)
            if not p.exists():
                records[label][str(p)] = "MISSING"
                lines.append(f"MISSING{' ' * 57}  {p}")
                continue
            digest = biterm_domain.file_digest(p)
            records[label][str(p)] = {"sha256": digest, "bytes": p.stat().st_size}
            lines.append(f"{digest}  {p}")
    (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    manifest = {"run_id": run_id(), "actor": actor(),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **(extra or {}), **records}
    (out_dir / "evidence_manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def write_atomic(path, text):
    """Write via a temp file + rename.

    A torn write of state.json is worse than no write: the next cycle reads it as the
    baseline and every finding in the truncated tail looks remediated.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".partial")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path
