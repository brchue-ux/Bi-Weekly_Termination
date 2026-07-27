"""Minimal xlsx reader (zip + sheet XML), used because the project runtime is system
python with no openpyxl.

Three defects fixed on 2026-07-26, all of the "silently wrong" kind:

  1. ROWS WERE POSITIONAL. Rows were appended in document order, but Excel OMITS empty
     rows entirely — a sheet whose row 2 is blank writes r="1" then r="3". Every consumer
     that read a fixed index (`rows[1]` as the header) therefore read a different row than
     the file's row 2, and every column mapping downstream shifted with it. Rows are now
     placed by their `r` attribute, with gaps filled by empty rows so indices mean what
     they say.
  2. NUMBER FORMATS WERE IGNORED. A date-formatted cell returned its raw serial, so an
     exception expiry arrived as "46234" and the control compared "46234" < "2026-07-26"
     as strings — False — and a lapsed exception silently passed. Date-formatted cells are
     now resolved through xl/styles.xml and returned as ISO YYYY-MM-DD.
  3. The ZipFile was never closed, shared-string indices were unguarded, and boolean/error
     cell types fell through to `None`.

Known landmines still worth remembering: <sheet> r:id uses the officeDocument relationship
namespace, and the header-row offset varies per tab — which is why consumers should locate
their header by CONTENT (see `find_header_row`) rather than by a fixed index.
"""
import datetime as dt
import re
import zipfile
import xml.etree.ElementTree as ET

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL_OFFDOC = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

# Built-in numFmtIds that denote a date and/or time (ECMA-376 §18.8.30).
BUILTIN_DATE_FORMATS = frozenset(
    list(range(14, 23)) + list(range(27, 37)) + list(range(45, 48)) + list(range(50, 59)))
EXCEL_EPOCH = dt.date(1899, 12, 30)   # accounts for Excel's 1900 leap-year bug
_DATE_TOKEN = re.compile(r"(\[[^\]]*\])|(\"[^\"]*\")|([ymdhs])", re.IGNORECASE)


class XlsxError(ValueError):
    """The workbook is not readable in the way the caller requires."""


def col_to_idx(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _is_date_format(code):
    """True when a custom format code denotes a date, ignoring literals and colour tokens."""
    if not code:
        return False
    for bracket, quoted, token in _DATE_TOKEN.findall(code):
        if token:
            return True
    return False


def _date_style_ids(z):
    """Set of cell-style indices (s="…") whose number format is a date."""
    if "xl/styles.xml" not in z.namelist():
        return set()
    root = ET.fromstring(z.read("xl/styles.xml"))
    custom = {}
    fmts = root.find(f"{NS_MAIN}numFmts")
    if fmts is not None:
        for f in fmts.findall(f"{NS_MAIN}numFmt"):
            try:
                custom[int(f.get("numFmtId"))] = f.get("formatCode") or ""
            except (TypeError, ValueError):
                continue
    date_styles = set()
    xfs = root.find(f"{NS_MAIN}cellXfs")
    if xfs is None:
        return date_styles
    for i, xf in enumerate(xfs.findall(f"{NS_MAIN}xf")):
        try:
            fmt_id = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        if fmt_id in BUILTIN_DATE_FORMATS or _is_date_format(custom.get(fmt_id)):
            date_styles.add(i)
    return date_styles


def _serial_to_iso(raw):
    """Excel serial -> ISO date string; returns the raw text if it is not a plausible date."""
    try:
        serial = float(raw)
    except (TypeError, ValueError):
        return raw
    if not (1 <= serial <= 2958465):
        return raw
    d = EXCEL_EPOCH + dt.timedelta(days=int(serial))
    frac = serial - int(serial)
    if frac:
        secs = int(round(frac * 86400))
        return f"{d.isoformat()}T{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
    return d.isoformat()


def load_workbook_rows(path):
    """Return {sheet_name: [row, ...]} where row is a dict {col_idx: value}.

    Rows are indexed by true worksheet position: `rows[0]` is spreadsheet row 1, and an
    omitted (empty) row is materialised as `{}` rather than shifting everything after it.
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for required in ("xl/workbook.xml", "xl/_rels/workbook.xml.rels"):
            if required not in names:
                raise XlsxError(f"{path}: not a readable xlsx (missing {required})")
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {r.get("Id"): r.get("Target") for r in rels}

        shared = []
        if "xl/sharedStrings.xml" in names:
            ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in ss:
                shared.append("".join(t.text or "" for t in si.iter(f"{NS_MAIN}t")))
        date_styles = _date_style_ids(z)

        sheets = {}
        sheet_els = wb.find(f"{NS_MAIN}sheets")
        if sheet_els is None:
            raise XlsxError(f"{path}: workbook declares no sheets")
        for sheet in sheet_els:
            name = sheet.get("name")
            rid = sheet.get(NS_REL_OFFDOC)
            if rid not in rid_to_target:
                raise XlsxError(f"{path}: sheet {name!r} has no resolvable relationship {rid!r}")
            target = rid_to_target[rid]
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = "xl/" + target
            if target not in names:
                raise XlsxError(f"{path}: sheet {name!r} points at missing part {target!r}")
            sheets[name] = _read_sheet(z.read(target), shared, date_styles, name)
        return sheets


def _read_sheet(xml_bytes, shared, date_styles, sheet_name):
    root = ET.fromstring(xml_bytes)
    by_index = {}
    max_row = 0
    implicit = 0
    for row_el in root.iter(f"{NS_MAIN}row"):
        r_attr = row_el.get("r")
        if r_attr and r_attr.isdigit():
            idx = int(r_attr) - 1
        else:                       # some producers omit r; fall back to document order
            idx = implicit
        implicit = idx + 1
        max_row = max(max_row, idx)
        row = {}
        for c in row_el.iter(f"{NS_MAIN}c"):
            ref = c.get("r") or ""
            m = re.match(r"([A-Z]+)(\d+)", ref)
            if not m:
                continue
            ci = col_to_idx(m.group(1))
            row[ci] = _cell_value(c, shared, date_styles, sheet_name, ref)
        by_index[idx] = row
    return [by_index.get(i, {}) for i in range(max_row + 1)]


def _cell_value(c, shared, date_styles, sheet_name, ref):
    t = c.get("t")
    if t == "inlineStr":
        is_el = c.find(f"{NS_MAIN}is")
        return "".join(x.text or "" for x in is_el.iter(f"{NS_MAIN}t")) if is_el is not None else ""
    v_el = c.find(f"{NS_MAIN}v")
    if v_el is None or v_el.text is None:
        return ""
    raw = v_el.text
    if t == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError) as e:
            raise XlsxError(
                f"{sheet_name}!{ref}: shared-string index {raw!r} out of range") from e
    if t == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if t == "e":
        return raw            # surface Excel errors (#N/A, #REF!) instead of silent ""
    if t in (None, "n"):
        try:
            style = int(c.get("s", "-1"))
        except ValueError:
            style = -1
        if style in date_styles:
            return _serial_to_iso(raw)
    return raw


# ---------------------------------------------------------------- header helpers

def find_header_row(rows, required, sheet_name="", search_limit=10):
    """Locate the header row by CONTENT and return (index, {header_name: column_index}).

    Consumers previously hardcoded `rows[1]`, which silently read the wrong row whenever a
    tab's header offset differed or a leading row was blank. Searching for the columns the
    caller actually needs makes the failure loud and names the tab.
    """
    required = list(required)
    for idx, row in enumerate(rows[:search_limit]):
        headers = {str(v).strip(): k for k, v in row.items() if str(v).strip()}
        if all(h in headers for h in required):
            return idx, headers
    seen = [sorted(str(v).strip() for v in rows[i].values() if str(v).strip())[:12]
            for i in range(min(search_limit, len(rows)))]
    raise XlsxError(
        f"{sheet_name or 'sheet'}: no header row in the first {search_limit} rows contains "
        f"all of {required}. Rows seen: {seen}")


def column(headers, *candidates, sheet_name=""):
    """Resolve the first present header name to its column index, or raise naming all tried.

    Replaces `[c for c in cols if c.endswith(...)][0]`, which raised a bare IndexError with
    no indication of which tab or which column was missing.
    """
    for name in candidates:
        if name in headers:
            return headers[name]
    for name in candidates:                       # tolerate case/spacing drift, explicitly
        for have in headers:
            if have.strip().lower() == name.strip().lower():
                return headers[have]
    raise XlsxError(
        f"{sheet_name or 'sheet'}: none of {list(candidates)} found among columns "
        f"{sorted(headers)}")


def column_by_suffix(headers, suffixes, sheet_name=""):
    """Resolve a column whose name ends with one of `suffixes` (e.g. per-app alias columns)."""
    matches = sorted(h for h in headers if h.endswith(tuple(suffixes)))
    if not matches:
        raise XlsxError(
            f"{sheet_name or 'sheet'}: no column ending in {list(suffixes)} among "
            f"{sorted(headers)}")
    if len(matches) > 1:
        raise XlsxError(
            f"{sheet_name or 'sheet'}: ambiguous alias column — {matches} all end in "
            f"{list(suffixes)}. Disambiguate before trusting the join.")
    return headers[matches[0]]
