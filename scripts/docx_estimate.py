#!/usr/bin/env python3
"""Estimate the page count of a .docx produced by docx_write.py, without a renderer.

SUPERSEDED for final answers — LibreOffice is installed now, so measure instead of estimating:

    soffice --headless --convert-to pdf --outdir <dir> <file>.docx && pdfinfo <dir>/<file>.pdf

This model proved unreliable at the margin (it called a real 9-page draft "6"), because it
cannot see how Word breaks inside tables. Keep it only for a fast smell test while iterating;
never report a page count from it. It walks document.xml in order, converts paragraphs and
table rows to heights in points from the metrics docx_write.py emits, and flows them into
fixed-height pages, honouring hard breaks.

Rendering fidelity note: pagination matches Word only when metric-compatible fonts are present.
Install Carlito (Calibri's metric twin) or LibreOffice substitutes a wider face and reports
~15% more pages than Word does.

Usage: python3 scripts/docx_estimate.py docs/<file>.docx [-v]
"""
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from math import ceil

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Page geometry emitted by docx_write.save(): Letter, 0.75in margins, 0.42in footer allowance.
TEXT_HEIGHT_PT = (11.0 - 0.75 - 0.75) * 72 - 30      # ≈ 654 pt of usable column height
TEXT_WIDTH_PT = (8.5 - 0.75 - 0.75) * 72             # 468 pt

# Calibri renders at roughly 0.47em average advance for mixed sentence case.
AVG_CHAR_EM = 0.47
LINE_FACTOR = 1.10 * 1.17    # w:line 264 (1.1x) applied to Calibri's ~1.17em default leading


def _sz(el):
    """Point size of the first run in a paragraph/cell (half-points in OOXML)."""
    for szel in el.iter(W + "sz"):
        return int(szel.get(W + "val")) / 2
    return 10.5


def _text_len(el):
    return sum(len(t.text or "") for t in el.iter(W + "t"))


def _ind(p):
    pr = p.find(W + "pPr")
    if pr is None:
        return 0
    i = pr.find(W + "ind")
    if i is None:
        return 0
    return int(i.get(W + "left", "0")) / 20.0    # twips → pt


def _spacing(p):
    pr = p.find(W + "pPr")
    if pr is None:
        return 0.0, 0.0
    s = pr.find(W + "spacing")
    if s is None:
        return 0.0, 6.0
    return int(s.get(W + "before", "0")) / 20.0, int(s.get(W + "after", "0")) / 20.0


def _has_break(p):
    pr = p.find(W + "pPr")
    if pr is not None and pr.find(W + "pageBreakBefore") is not None:
        return True
    return any(b.get(W + "type") == "page" for b in p.iter(W + "br"))


def para_height(p, width_pt=TEXT_WIDTH_PT):
    size = _sz(p)
    chars = _text_len(p)
    usable = max(width_pt - _ind(p), 60)
    cpl = max(int(usable / (size * AVG_CHAR_EM)), 8)
    lines = max(ceil(chars / cpl), 1) if chars else 1
    before, after = _spacing(p)
    return lines * size * LINE_FACTOR + before + after


def table_height(tbl):
    grid = [int(g.get(W + "w")) for g in tbl.find(W + "tblGrid")]
    total = sum(grid) or 1
    h = 0.0
    for tr in tbl.findall(W + "tr"):
        row = 0.0
        for i, tc in enumerate(tr.findall(W + "tc")):
            colw = grid[i] if i < len(grid) else total // max(len(grid), 1)
            cell_pt = colw / total * TEXT_WIDTH_PT - 12          # cell margins L+R
            cell = sum(para_height(p, width_pt=max(cell_pt, 40)) for p in tc.findall(W + "p"))
            row = max(row, cell)
        h += row + 8                                             # cell margins top+bottom
    return h


def estimate(path, verbose=False):
    z = zipfile.ZipFile(path)
    body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
    pages, cur, log = 1, 0.0, []
    for el in body:
        tag = el.tag.replace(W, "")
        if tag == "p":
            if _has_break(el) and cur > 0:
                log.append((pages, round(cur), "— hard break"))
                pages, cur = pages + 1, 0.0
            h = para_height(el)
        elif tag == "tbl":
            h = table_height(el)
        else:
            continue
        if cur + h > TEXT_HEIGHT_PT:
            log.append((pages, round(cur), "— full"))
            pages, cur = pages + 1, 0.0
        cur += h
        if verbose:
            txt = "".join(t.text or "" for t in el.iter(W + "t"))[:58]
            print(f"  p{pages:>2} {cur:6.0f}/{TEXT_HEIGHT_PT:.0f}pt  {tag:4} {txt}")
    log.append((pages, round(cur), "— end"))
    return pages, log


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    pages, log = estimate(args[0], verbose="-v" in sys.argv)
    for pg, used, why in log:
        print(f"  page {pg:>2}: {used:>4} pt used {why}")
    print(f"\nESTIMATED PAGES: {pages}   ({args[0]})")
