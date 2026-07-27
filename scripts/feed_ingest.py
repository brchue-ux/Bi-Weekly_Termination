"""Ingest the unjoined scheduled drops into the shapes biweekly_recon already consumes.

This is an ADAPTER, deliberately not a second pipeline. The classifier, risk tiers, closure
logic and ServiceNow integration in biweekly_recon are already verified; swapping the input
layer is the only change the new feed model actually requires. Anything that reimplemented
classification here would be a second control to test and keep in sync.

What it models, card for card, is the front half of the Okta Workflows flow:

    read each app drop  ->  normalise  ->  join to the HR drop  ->  hand off

The join that today's STARS workbook arrives with already done is performed HERE, per cycle,
from two independent files -- which is the entire point of the future-state design.

Point-in-time correctness (fixed 2026-07-26): the exception register is now selected BY
CYCLE STAMP, not by "newest file on disk". Re-running cycle 20260723 used to evaluate it
against today's exception list and produce a different answer than the cycle originally
reported — a SOX control has to be reproducible from its stated inputs. Every input file
that was actually read is returned in `meta["input_files"]` so the cycle can hash them.
"""
import csv
import datetime as dt
from pathlib import Path

import biterm_domain as domain

HR_DIR = "_HR_TalentHub"
REF_DIR = "_reference"

# Required columns per feed. Declared so a schema drift fails at load with a clear message
# instead of raising KeyError from deep inside the row loop, halfway through an app.
HR_COLUMNS = ("upn", "employment_status", "employee_id")
APP_COLUMNS = ("account_id", "email")
EXCEPTION_COLUMNS = ("application", "upn", "owner_upn", "expiry", "exception_type")


class FeedError(RuntimeError):
    """A drop is incomplete, malformed, or internally inconsistent."""


def _read(path, required=()):
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise FeedError(f"{path}: missing required column(s) {missing}; "
                            f"found {reader.fieldnames}")
        return list(reader)


def app_folders(feeds_dir):
    """Drop zones only: underscore-prefixed folders are feeds/reference, not applications."""
    return sorted(d for d in Path(feeds_dir).iterdir()
                  if d.is_dir() and not d.name.startswith("_"))


def available_stamps(feeds_dir):
    """Cycle stamps that have BOTH an HR drop and at least one app drop."""
    hr = {p.stem.rsplit("_", 1)[-1] for p in (Path(feeds_dir) / HR_DIR).glob("TalentHub_HR_*.csv")}
    app = {p.stem.rsplit("_", 1)[-1]
           for d in app_folders(feeds_dir) for p in d.glob("*_users_*.csv")}
    return sorted(hr & app)


def exception_file_for(feeds_dir, stamp):
    """The exception register in force AS OF this cycle: newest file whose stamp <= cycle.

    Taking `sorted(glob)[-1]` meant a re-run of an old cycle silently used a newer register.
    """
    candidates = []
    for p in sorted((Path(feeds_dir) / REF_DIR).glob("exception_list_*.csv")):
        file_stamp = p.stem.rsplit("_", 1)[-1]
        if file_stamp.isdigit() and file_stamp <= stamp:
            candidates.append((file_stamp, p))
    return candidates[-1][1] if candidates else None


def load(feeds_dir, stamp=None):
    """Return (populations, hr_by_upn, exceptions, meta) for one cycle's drop.

    populations[app] = [{key, legacy_key, alias, upn, empid, hr, src}] — the shape
    biweekly_recon.load_rosters produces.
    """
    feeds_dir = Path(feeds_dir)
    stamps = available_stamps(feeds_dir)
    if not stamps:
        raise FeedError(f"no complete drop (HR + app files) found under {feeds_dir}")
    stamp = stamp or stamps[-1]
    if stamp not in stamps:
        raise FeedError(f"drop {stamp} incomplete or absent; available: {stamps}")

    hr_path = feeds_dir / HR_DIR / f"TalentHub_HR_{stamp}.csv"
    hr_rows = _read(hr_path, HR_COLUMNS)
    input_files = [str(hr_path)]

    # Normalisation is a real step, not a formality: the app feeds carry mixed-case addresses
    # and a join on raw strings would silently miss almost every row.
    hr_by_upn, hr_full, dup_hr = {}, {}, []
    for r in hr_rows:
        upn = r["upn"].strip().lower()
        if not upn:
            continue
        if upn in hr_full:
            dup_hr.append(upn)
        hr_by_upn[upn] = r["employment_status"].strip()
        hr_full[upn] = r
    if dup_hr:
        # Last-wins on a duplicated HR identity silently decides someone's employment status.
        raise FeedError(f"{hr_path}: duplicate UPN(s) in the HR drop: {sorted(set(dup_hr))[:10]}"
                        f" ({len(set(dup_hr))} total). Resolve before running a cycle.")

    populations, stats = {}, {"apps": 0, "rows": 0, "joined": 0, "unjoinable": 0, "no_hr_match": 0}
    missing = []
    for folder in app_folders(feeds_dir):
        path = folder / f"{folder.name.replace(' ', '_')}_users_{stamp}.csv"
        if not path.exists():
            # A drop that never landed must be loud. Treating it as an empty app would look
            # like every account vanished and hand the closure pass 100% false closures.
            missing.append(folder.name)
            continue
        input_files.append(str(path))
        pop = []
        for row in _read(path, APP_COLUMNS):
            alias = row["account_id"].strip()
            email = row["email"].strip().lower()
            upn = domain.normalise_upn(email)
            hr_row = hr_full.get(upn)
            if not upn:
                stats["unjoinable"] += 1
            elif hr_row:
                stats["joined"] += 1
            else:
                stats["no_hr_match"] += 1
            entry = {
                "alias": alias,
                "upn": upn,
                "empid": (hr_row or {}).get("employee_id", ""),
                # No HR row is not an unrecognised status — it is the "not in TalentHub"
                # case, and saying so routes it to the right loud-unknown reason string.
                "hr": (hr_row or {}).get("employment_status", "").strip() or domain.HR_NOT_FOUND,
                "src": {**row, **{f"HR_{k}": v for k, v in (hr_row or {}).items()}},
            }
            entry["key"] = domain.identity_key(entry)
            entry["legacy_key"] = domain.legacy_identity_key(entry)
            pop.append(entry)
        populations[folder.name] = pop
        stats["apps"] += 1
        stats["rows"] += len(pop)
    if missing:
        raise FeedError(f"drop {stamp} missing app export(s): {missing}")

    exc_path = exception_file_for(feeds_dir, stamp)
    exceptions, problems = {}, []
    if exc_path:
        input_files.append(str(exc_path))
        for n, r in enumerate(_read(exc_path, EXCEPTION_COLUMNS), start=2):
            upn = r["upn"].strip().lower()
            if not upn:
                continue
            try:
                expiry = domain.parse_date(r["expiry"].strip(),
                                           field=f"{exc_path.name} row {n} expiry")
            except domain.DateFormatError as e:
                problems.append(str(e))
                continue
            exceptions.setdefault(r["application"].strip(), {})[upn] = {
                "owner": r["owner_upn"].strip().lower(),
                "expiry": expiry,
                "expiry_raw": r["expiry"].strip(),
                "type": r["exception_type"].strip(),
            }
    if problems:
        raise FeedError("exception register is not usable — fix the source file and re-run:\n  "
                        + "\n  ".join(problems[:25]))

    meta = {"stamp": stamp,
            "cycle_date": dt.datetime.strptime(stamp, "%Y%m%d").date().isoformat(),
            "hr_rows": len(hr_rows),
            "exception_file": exc_path.name if exc_path else "(none)",
            "input_files": input_files,
            **stats}
    return populations, hr_by_upn, exceptions, meta
