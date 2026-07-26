"""Ingest the unjoined scheduled drops into the shapes biweekly_recon already consumes.

This is an ADAPTER, deliberately not a second pipeline. The classifier, risk tiers, closure
logic and ServiceNow integration in biweekly_recon are already verified; swapping the input
layer is the only change the new feed model actually requires. Anything that reimplemented
classification here would be a second control to test and keep in sync.

What it models, card for card, is the front half of the Okta Workflows flow:

    read each app drop  ->  normalise  ->  join to the HR drop  ->  hand off

The join that today's STARS workbook arrives with already done is performed HERE, per cycle,
from two independent files -- which is the entire point of the future-state design.
"""
import csv
import datetime as dt
from pathlib import Path

from seed_tenant import NO_UPN

HR_DIR = "_HR_TalentHub"
REF_DIR = "_reference"


def _read(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


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


def load(feeds_dir, stamp=None):
    """Return (populations, hr_by_upn, exceptions, meta) for one cycle's drop.

    populations[app] = [{key, alias, upn, empid, hr, src}] — biweekly_recon.load_rosters shape.
    """
    feeds_dir = Path(feeds_dir)
    stamps = available_stamps(feeds_dir)
    if not stamps:
        raise RuntimeError(f"no complete drop (HR + app files) found under {feeds_dir}")
    stamp = stamp or stamps[-1]
    if stamp not in stamps:
        raise RuntimeError(f"drop {stamp} incomplete or absent; available: {stamps}")

    hr_rows = _read(feeds_dir / HR_DIR / f"TalentHub_HR_{stamp}.csv")
    # Normalisation is a real step, not a formality: the app feeds carry mixed-case addresses
    # and a join on raw strings would silently miss almost every row.
    hr_by_upn = {r["upn"].strip().lower(): r["employment_status"].strip()
                 for r in hr_rows if r["upn"].strip()}
    hr_full = {r["upn"].strip().lower(): r for r in hr_rows if r["upn"].strip()}

    populations, stats = {}, {"apps": 0, "rows": 0, "joined": 0, "unjoinable": 0, "no_hr_match": 0}
    missing = []
    for folder in app_folders(feeds_dir):
        path = folder / f"{folder.name.replace(' ', '_')}_users_{stamp}.csv"
        if not path.exists():
            # A drop that never landed must be loud. Treating it as an empty app would look
            # like every account vanished and hand the closure pass 100% false closures.
            missing.append(folder.name)
            continue
        pop = []
        for row in _read(path):
            alias = row["account_id"].strip()
            email = row["email"].strip().lower()
            upn = email if "@" in email else ""
            hr_row = hr_full.get(upn)
            if not upn:
                stats["unjoinable"] += 1
            elif hr_row:
                stats["joined"] += 1
            else:
                stats["no_hr_match"] += 1
            pop.append({
                "key": upn or f"alias:{alias}",
                "alias": alias,
                "upn": upn,
                "empid": (hr_row or {}).get("employee_id", ""),
                # No HR row is not an unrecognised status — it is the "not in TalentHub"
                # case, and saying so routes it to the right loud-unknown reason string.
                "hr": (hr_row or {}).get("employment_status", "").strip() or NO_UPN,
                "src": {**row, **{f"HR_{k}": v for k, v in (hr_row or {}).items()}},
            })
        populations[folder.name] = pop
        stats["apps"] += 1
        stats["rows"] += len(pop)
    if missing:
        raise RuntimeError(f"drop {stamp} missing app export(s): {missing}")

    exc_files = sorted((feeds_dir / REF_DIR).glob("exception_list_*.csv"))
    exceptions = {}
    for r in _read(exc_files[-1]) if exc_files else []:
        upn = r["upn"].strip().lower()
        if upn:
            exceptions.setdefault(r["application"].strip(), {})[upn] = {
                "owner": r["owner_upn"].strip().lower(),
                "expiry": r["expiry"].strip(),
                "type": r["exception_type"].strip(),
            }

    meta = {"stamp": stamp, "cycle_date": dt.datetime.strptime(stamp, "%Y%m%d").date().isoformat(),
            "hr_rows": len(hr_rows), "exception_file": exc_files[-1].name if exc_files else "(none)",
            **stats}
    return populations, hr_by_upn, exceptions, meta
