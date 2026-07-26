"""Minimal xlsx writer: multi-sheet, inline strings only (no sharedStrings)."""
import zipfile
from xml.sax.saxutils import escape

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, sheets):
    """sheets: list of (name, rows) where rows is a list of lists of str."""
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    wb = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>']
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    sheet_xmls = []
    for i, (name, rows) in enumerate(sheets, 1):
        ct.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        wb.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
        rels.append(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>')
        body = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}"><sheetData>']
        for rn, row in enumerate(rows, 1):
            cells = "".join(
                f'<c r="{_col(ci)}{rn}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'
                for ci, v in enumerate(row))
            body.append(f'<row r="{rn}">{cells}</row>')
        body.append("</sheetData></worksheet>")
        sheet_xmls.append("".join(body))
    ct.append("</Types>")
    wb.append("</sheets></workbook>")
    rels.append("</Relationships>")
    pkg_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", pkg_rels)
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        for i, xml in enumerate(sheet_xmls, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
