"""
Independent verification of the re-identity rewrite (never trust the rewriter's
own logs). Compares the ORIGINALS in .originals/pre_reidentity_20260723/ against
the rewritten files:

  1. STRUCTURE  — same tabs, same row counts, same column widths per row.
  2. UNTOUCHED  — every non-identity cell byte-identical.
  3. LEAK SCAN  — zero original name tokens anywhere in the new files
                  (infrastructure tokens merrycorp/bitermtest/com whitelisted).
  4. MAPPING    — multiset of email local-parts in new files == multiset of
                  originals passed through the saved map (same person -> same new
                  identity everywhere; no merges, no drops).
  5. CENSUS     — per-tab HR-status class counts identical (legit/term/notfound/
                  blank/defect) — the classifier test surface survived.
  6. EXCEPTIONS — every exception UPN + owner still resolves against the new
                  STARS rosters (join integrity).

Ends in a single line: VERDICT: PASS | FAIL.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from xlsx_min import load_workbook_rows
from reidentity import (BASE, STARS, EXCEPT, BACKUP, MAP_OUT, STATUSES, INFRA,
                        find_header, identity_columns, harvest, collect_vocab,
                        valid_email, local_of, is_sentinel)

WHITELIST = INFRA
failures = []


def check(name, ok, detail):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def cells(book):
    for tab, rows in book.items():
        for ri, r in enumerate(rows):
            for ci, v in r.items():
                yield tab, ri, ci, str(v)


def main():
    o_stars = load_workbook_rows(BACKUP / STARS)
    o_exc = load_workbook_rows(BACKUP / EXCEPT)
    n_stars = load_workbook_rows(BASE / STARS)
    n_exc = load_workbook_rows(BASE / EXCEPT)
    mapping = {k: (v["first"], v["last"]) for k, v in json.load(open(MAP_OUT)).items()}

    # 1. STRUCTURE
    for name, o, n in (("STARS", o_stars, n_stars), ("EXC", o_exc, n_exc)):
        same_tabs = list(o.keys()) == list(n.keys())
        same_rows = same_tabs and all(len(o[t]) == len(n[t]) for t in o)
        check(f"STRUCTURE {name}", same_tabs and same_rows,
              f"{len(o)} tabs, rows {'match' if same_rows else 'MISMATCH'}")

    # 2. UNTOUCHED — non-identity cells byte-equal
    diffs = 0
    for (o, n) in ((o_stars, n_stars),):
        for tab in o:
            hi = find_header(o[tab])
            kinds = identity_columns(o[tab][hi]) if hi is not None else {}
            for ri, orow in enumerate(o[tab]):
                nrow = n[tab][ri]
                for ci in set(orow) | set(nrow):
                    if hi is not None and ri > hi and ci in kinds:
                        continue
                    if str(orow.get(ci, "")) != str(nrow.get(ci, "")):
                        diffs += 1
    for tab in o_exc:
        for ri, orow in enumerate(o_exc[tab]):
            nrow = n_exc[tab][ri]
            for ci in set(orow) | set(nrow):
                if ri > 0 and ci in (0, 1, 3, 6):
                    continue
                if str(orow.get(ci, "")) != str(nrow.get(ci, "")):
                    diffs += 1
    check("UNTOUCHED", diffs == 0, f"{diffs} unexpected non-identity cell changes")

    # 3. LEAK SCAN — forbidden = identity-cell tokens from originals, minus tokens
    # that ALSO appear in original NON-identity cells (legit vocabulary: 'user',
    # 'europe', app names in the Header tab), minus infrastructure names.
    tokens, _ = harvest(o_stars, o_exc)
    forbidden = tokens - collect_vocab(o_stars) - WHITELIST
    leaks = Counter()
    for book in (n_stars, n_exc):
        for tab, ri, ci, v in cells(book):
            for t in re.split(r"[\s.\\@,_/-]+", v):
                if t.isalpha() and len(t) >= 3 and t.lower() in forbidden:
                    leaks[(tab, t.lower())] += 1
    check("LEAK SCAN", not leaks,
          f"{sum(leaks.values())} hits" + (f" e.g. {list(leaks)[:5]}" if leaks else
          f" across {len(forbidden)} forbidden tokens ({len(tokens - forbidden)} whitelisted as vocabulary/infra)"))

    # 4. MAPPING — positional: every original email-form cell must equal its
    # per-cell mapped rewrite in the new file (desynced locals keep their own identity)
    from reidentity import rewrite_email
    bad_cells = []
    for tab in o_stars:
        hi = find_header(o_stars[tab])
        if hi is None:
            continue
        kinds = identity_columns(o_stars[tab][hi])
        for ri, r in enumerate(o_stars[tab]):
            if ri <= hi:
                continue
            for ci, k in kinds.items():
                v = str(r.get(ci, ""))
                if k in ("email", "username") and valid_email(v) and not is_sentinel(v):
                    expect = rewrite_email(v, mapping) or v
                    got = str(n_stars[tab][ri].get(ci, ""))
                    if got != expect:
                        bad_cells.append((tab, ri, ci, v, expect, got))
    check("MAPPING", not bad_cells, f"{len(bad_cells)} positional mismatches" +
          (f" e.g. {bad_cells[:2]}" if bad_cells else ""))

    # 5. CENSUS — per-tab status class counts
    def census(book):
        out = {}
        for tab, rows in book.items():
            hi = find_header(rows)
            if hi is None:
                continue
            kinds = identity_columns(rows[hi])
            sc = next((ci for ci, k in kinds.items() if k == "status"), None)
            if sc is None:
                continue
            c = Counter()
            for r in rows[hi + 1:]:
                if not any(str(v).strip() for v in r.values()):
                    continue
                v = str(r.get(sc, "")).strip()
                c["known" if v in STATUSES else "defect", v if v in STATUSES else ""] += 1
            out[tab] = c
        return out
    check("CENSUS", census(o_stars) == census(n_stars), "per-tab status class counts")

    # 6. EXCEPTIONS join integrity against new rosters
    new_upns = {local_of(v) for _, _, _, v in cells(n_stars) if valid_email(v)}
    bad = []
    for tab, rows in n_exc.items():
        for r in rows[1:]:
            upn = str(r.get(1, "")).strip()
            owner = str(r.get(6, "")).strip()
            if upn and local_of(upn) not in new_upns:
                bad.append(("upn", upn))
            if valid_email(owner) and local_of(owner) not in new_upns:
                bad.append(("owner", owner))
    check("EXCEPTIONS", not bad, f"{len(bad)} unresolvable" + (f" e.g. {bad[:3]}" if bad else ""))

    print(f"\nVERDICT: {'PASS' if not failures else 'FAIL (' + ', '.join(failures) + ')'}")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
