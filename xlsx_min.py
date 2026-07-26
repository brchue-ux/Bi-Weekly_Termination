"""Minimal xlsx reader (zip + sheet XML, cells placed by their r attribute).

Known landmines (project CLAUDE.md): <sheet> r:id uses the officeDocument
relationship namespace; header row offset varies per tab; shared strings.
"""
import re
import zipfile
import xml.etree.ElementTree as ET

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL_OFFDOC = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def col_to_idx(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def load_workbook_rows(path):
    """Return {sheet_name: [row, ...]} where row is a dict {col_idx: value}."""
    z = zipfile.ZipFile(path)
    # sheet name -> target xml path, via workbook rels
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {
        r.get("Id"): r.get("Target")
        for r in rels
    }
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in ss:
            shared.append("".join(t.text or "" for t in si.iter(f"{NS_MAIN}t")))
    sheets = {}
    for sheet in wb.find(f"{NS_MAIN}sheets"):
        name = sheet.get("name")
        target = rid_to_target[sheet.get(NS_REL_OFFDOC)]
        if not target.startswith("xl/"):
            target = "xl/" + target
        root = ET.fromstring(z.read(target))
        rows = []
        for row_el in root.iter(f"{NS_MAIN}row"):
            row = {}
            for c in row_el.iter(f"{NS_MAIN}c"):
                ref = c.get("r") or ""
                m = re.match(r"([A-Z]+)(\d+)", ref)
                if not m:
                    continue
                ci = col_to_idx(m.group(1))
                t = c.get("t")
                v_el = c.find(f"{NS_MAIN}v")
                if t == "inlineStr":
                    is_el = c.find(f"{NS_MAIN}is")
                    val = "".join(t2.text or "" for t2 in is_el.iter(f"{NS_MAIN}t")) if is_el is not None else ""
                elif v_el is None:
                    val = ""
                elif t == "s":
                    val = shared[int(v_el.text)]
                else:
                    val = v_el.text
                row[ci] = val
            rows.append(row)
        sheets[name] = rows
    return sheets
