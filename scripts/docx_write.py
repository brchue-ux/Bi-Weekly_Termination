"""Minimal, dependency-free WordprocessingML (.docx) writer.

Same reason as xlsx_min/xlsx_write exist: this environment has no python-docx, no lxml, no pip
and no pandoc, so the document is assembled as raw OOXML + zip. Scope is deliberately narrow —
exactly the constructs the BiTerm build lab needs (headings, styled body text, bullet lists,
banded tables, shaded callout panels, box-and-arrow figures, a title page and a page-number
footer) and nothing else.

Two rules the OOXML schema enforces that are easy to get wrong and expensive to debug:
  * child elements of <w:pPr>/<w:rPr>/<w:tcPr>/<w:tblPr> are SEQUENCES — order is validated,
    and Word rejects the file (rather than degrading) when it is wrong. The emitters below
    build each properties element in schema order for that reason.
  * every part must be declared in [Content_Types].xml AND reachable through a rels file.
"""
import zipfile
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"

# Palette — one place, so the document reads as a single designed system.
NAVY = "1B3A6B"      # headings, title
STEEL = "44546A"     # subheads, table header fill text
TEAL = "0F6E5C"      # "benefit" accent
AMBER = "9C6B15"     # "in flight" / caution accent
CRIMSON = "9C4A4A"   # "danger"/landmine accent
INK = "23272E"       # body text
MUTED = "5A6270"     # secondary text
RULE = "C7CDD6"      # hairlines
BAND = "F4F6F9"      # zebra banding / panel fill
BAND2 = "EAEEF4"     # header fill

BODY_FONT = "Calibri"
HEAD_FONT = "Calibri Light"
MONO_FONT = "Consolas"


def _t(s):
    return f'<w:t xml:space="preserve">{escape(s)}</w:t>'


def _rpr(bold=False, italic=False, color=INK, size=21, font=BODY_FONT, caps=False,
         underline=False, shade=None, spacing=None):
    """size is in HALF-points (21 = 10.5pt), matching the OOXML unit."""
    x = [f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>']
    if bold:
        x.append("<w:b/>")
    if italic:
        x.append("<w:i/>")
    if caps:
        x.append("<w:caps/>")
    x.append(f'<w:color w:val="{color}"/>')
    if spacing is not None:
        x.append(f'<w:spacing w:val="{spacing}"/>')
    x.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if underline:
        x.append('<w:u w:val="single"/>')
    if shade:
        x.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    return "<w:rPr>" + "".join(x) + "</w:rPr>"


def run(text, **kw):
    return f"<w:r>{_rpr(**kw)}{_t(text)}</w:r>"


def rich(text, base=None):
    """Inline markup: **bold**, *italic*, `code`. Kept tiny on purpose."""
    base = dict(base or {})
    out, buf, i = [], "", 0

    def flush(**over):
        nonlocal buf
        if buf:
            kw = dict(base)
            kw.update(over)
            out.append(run(buf, **kw))
            buf = ""

    while i < len(text):
        if text.startswith("**", i):
            j = text.find("**", i + 2)
            if j > 0:
                flush()
                # Recurse so `code` and *italic* still resolve inside a bold span.
                out.append(rich(text[i + 2:j], dict(base, bold=True)))
                i = j + 2
                continue
        if text[i] == "`":
            j = text.find("`", i + 1)
            if j > 0:
                flush()
                kw = dict(base)
                kw.update(font=MONO_FONT, size=kw.get("size", 21) - 2,
                          color=kw.get("color", INK), shade=BAND)
                out.append(run(text[i + 1:j], **kw))
                i = j + 1
                continue
        if text[i] == "*" and not text.startswith("**", i):
            j = text.find("*", i + 1)
            if j > 0:
                flush()
                out.append(rich(text[i + 1:j], dict(base, italic=True)))
                i = j + 1
                continue
        buf += text[i]
        i += 1
    flush()
    return "".join(out)


def _ppr(style=None, keep_next=False, border_left=None, shade=None, before=0, after=120,
         line=None, ind_left=0, ind_hang=0, align=None, border_bottom=None, page_break=False,
         contextual=False):
    x = []
    if style:
        x.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        x.append("<w:keepNext/><w:keepLines/>")
    if page_break:
        x.append("<w:pageBreakBefore/>")
    bdr = ""
    if border_left:
        bdr += f'<w:left w:val="single" w:sz="24" w:space="8" w:color="{border_left}"/>'
    if border_bottom:
        bdr += f'<w:bottom w:val="single" w:sz="6" w:space="4" w:color="{border_bottom}"/>'
    if bdr:
        x.append(f"<w:pBdr>{bdr}</w:pBdr>")
    if shade:
        x.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    sp = f'<w:spacing w:before="{before}" w:after="{after}"'
    if line:
        sp += f' w:line="{line}" w:lineRule="auto"'
    x.append(sp + "/>")
    if ind_left or ind_hang:
        x.append(f'<w:ind w:left="{ind_left}" w:hanging="{ind_hang}"/>')
    # CT_PPr sequence puts contextualSpacing after ind and before jc — order is validated by Word.
    if contextual:
        x.append("<w:contextualSpacing/>")
    if align:
        x.append(f'<w:jc w:val="{align}"/>')
    return "<w:pPr>" + "".join(x) + "</w:pPr>"


class Docx:
    def __init__(self, title="Document", creator="", subject=""):
        self.body = []
        self.meta = (title, creator, subject)

    # ---------- block builders ----------
    def raw(self, xml):
        self.body.append(xml)

    def para(self, runs_xml, **ppr):
        self.body.append(f"<w:p>{_ppr(**ppr)}{runs_xml}</w:p>")

    def spacer(self, h=120):
        self.body.append(f'<w:p>{_ppr(after=h)}</w:p>')

    def tail(self):
        """Close the body with a 1pt paragraph.

        Word requires a paragraph after a trailing table, but the normal spacer is ~21pt — enough
        to push an otherwise-full page onto a second, blank one. This drops any trailing empty
        spacer and emits a height-less replacement instead.
        """
        if self.body and self.body[-1].startswith("<w:p>") and "<w:t" not in self.body[-1]:
            self.body.pop()
        self.body.append('<w:p><w:pPr><w:spacing w:before="0" w:after="0"/>'
                         '<w:rPr><w:sz w:val="2"/><w:szCs w:val="2"/></w:rPr></w:pPr></w:p>')

    def page_break(self):
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def title_block(self, title, subtitle, kicker=None):
        if kicker:
            self.para(run(kicker, color=TEAL, bold=True, size=19, caps=True, spacing=40),
                      after=80)
        self.para(run(title, font=HEAD_FONT, color=NAVY, size=56, bold=False),
                  after=60, keep_next=True)
        self.para(run(subtitle, font=HEAD_FONT, color=MUTED, size=26),
                  after=200, border_bottom=RULE)

    def h1(self, text, page_break=False):
        self.para(run(text, font=HEAD_FONT, color=NAVY, size=32),
                  before=(0 if page_break else 360), after=100, keep_next=True,
                  page_break=page_break, border_bottom=RULE)

    def h2(self, text):
        self.para(run(text, font=HEAD_FONT, color=STEEL, size=25),
                  before=260, after=90, keep_next=True)

    def h3(self, text):
        self.para(run(text, color=STEEL, size=21, bold=True, caps=True, spacing=20),
                  before=200, after=70, keep_next=True)

    def p(self, text, size=21, color=INK, after=120, ind_left=0, italic=False):
        self.para(rich(text, dict(size=size, color=color, italic=italic)),
                  after=after, line=264, ind_left=ind_left)

    def bullets(self, items, color=INK, size=21, ind=360, marker="▪"):
        for it in items:
            self.para(run(f"{marker}  ", color=MUTED, size=size) +
                      rich(it, dict(size=size, color=color)),
                      after=60, line=264, ind_left=ind + 200, ind_hang=200,
                      contextual=True)

    def numbered(self, items, color=INK, size=21):
        for n, it in enumerate(items, 1):
            self.para(run(f"{n}.  ", color=NAVY, size=size, bold=True) +
                      rich(it, dict(size=size, color=color)),
                      after=60, line=264, ind_left=560, ind_hang=280, contextual=True)

    # ---------- tables ----------
    def _tbl(self, grid, rows_xml, borders=True, indent=0):
        b = ""
        if borders:
            e = lambda k: f'<w:{k} w:val="single" w:sz="4" w:space="0" w:color="{RULE}"/>'
            b = ("<w:tblBorders>" + e("top") + e("left") + e("bottom") + e("right") +
                 e("insideH") + e("insideV") + "</w:tblBorders>")
        pr = ('<w:tblPr><w:tblW w:w="5000" w:type="pct"/>' +
              (f'<w:tblInd w:w="{indent}" w:type="dxa"/>' if indent else "") + b +
              '<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
              '<w:bottom w:w="80" w:type="dxa"/><w:right w:w="120" w:type="dxa"/>'
              '</w:tblCellMar><w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0"'
              ' w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>')
        g = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in grid) + "</w:tblGrid>"
        self.body.append(f"<w:tbl>{pr}{g}{rows_xml}</w:tbl>")
        self.spacer(140)

    @staticmethod
    def _cell(width, paras, shade=None, span=None, valign="center", border_left=None):
        x = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
        if span:
            x.append(f'<w:gridSpan w:val="{span}"/>')
        if border_left:
            x.append('<w:tcBorders>'
                     f'<w:left w:val="single" w:sz="24" w:space="0" w:color="{border_left}"/>'
                     '</w:tcBorders>')
        if shade:
            x.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
        x.append(f'<w:vAlign w:val="{valign}"/>')
        return f'<w:tc><w:tcPr>{"".join(x)}</w:tcPr>{paras}</w:tc>'

    def table(self, headers, rows, widths, zebra=True, head_fill=BAND2, size=19):
        total = sum(widths)
        grid = [round(w / total * 9360) for w in widths]
        out = []
        if headers:
            cells = "".join(
                self._cell(grid[i],
                           f'<w:p>{_ppr(after=0, before=0)}'
                           f'{rich(h, dict(bold=True, size=size, color=NAVY))}</w:p>',
                           shade=head_fill)
                for i, h in enumerate(headers))
            out.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>' + cells + "</w:tr>")
        for n, row in enumerate(rows):
            fill = BAND if (zebra and n % 2 == 1) else None
            cells = "".join(
                self._cell(grid[i],
                           f'<w:p>{_ppr(after=0, before=0, line=240)}'
                           f'{rich(c, dict(size=size, color=INK))}</w:p>', shade=fill)
                for i, c in enumerate(row))
            out.append("<w:tr>" + cells + "</w:tr>")
        self._tbl(grid, "".join(out))

    # ---------- callout panel ----------
    def callout(self, label, body_lines, accent=NAVY, fill=BAND, bullets=False):
        """One-cell table = a shaded panel with a thick accent bar down its left edge."""
        paras = [f'<w:p>{_ppr(after=60, before=0)}'
                 f'{rich(label, dict(bold=True, size=20, color=accent, caps=False))}</w:p>']
        for i, ln in enumerate(body_lines):
            last = i == len(body_lines) - 1
            if bullets:
                paras.append(
                    f'<w:p>{_ppr(after=(0 if last else 60), before=0, line=264, ind_left=380, ind_hang=200)}'
                    f'{run(chr(0x25AA) + "  ", color=accent, size=19)}'
                    f'{rich(ln, dict(size=20, color=INK))}</w:p>')
            else:
                paras.append(f'<w:p>{_ppr(after=(0 if last else 80), before=0, line=264)}'
                             f'{rich(ln, dict(size=20, color=INK))}</w:p>')
        cell = self._cell(9360, "".join(paras), shade=fill, valign="top", border_left=accent)
        self._tbl([9360], f"<w:tr>{cell}</w:tr>", borders=False)

    # ---------- figures (box-and-arrow, since Word cannot render mermaid) ----------
    def fig_caption(self, text):
        self.para(rich(text, dict(size=18, color=MUTED, italic=True)), after=100)

    def _box(self, width, lines, fill, accent, bold_first=True):
        paras = []
        for i, ln in enumerate(lines):
            paras.append(
                f'<w:p>{_ppr(after=(0 if i == len(lines) - 1 else 30), before=0, align="center", line=240)}'
                f'{rich(ln, dict(size=18, color=(accent if (i == 0 and bold_first) else INK), bold=(i == 0 and bold_first)))}</w:p>')
        return self._cell(width, "".join(paras), shade=fill, valign="center",
                          border_left=accent)

    def fig_row(self, boxes, arrow="→"):
        """boxes: list of (lines, fill, accent). Rendered left-to-right with arrows between."""
        n = len(boxes)
        arrow_w = 320
        box_w = (9360 - arrow_w * (n - 1)) // n
        grid, cells = [], []
        for i, (lines, fill, accent) in enumerate(boxes):
            if i:
                grid.append(arrow_w)
                cells.append(self._cell(
                    arrow_w,
                    f'<w:p>{_ppr(after=0, before=0, align="center")}'
                    f'{run(arrow, color=MUTED, size=22)}</w:p>'))
            grid.append(box_w)
            cells.append(self._box(box_w, lines, fill, accent))
        self._tbl(grid, "<w:tr>" + "".join(cells) + "</w:tr>", borders=False)

    def fig_stack(self, steps, arrow="▼"):
        """steps: list of (lines, fill, accent) stacked vertically with down-arrows."""
        rows = []
        for i, (lines, fill, accent) in enumerate(steps):
            if i:
                rows.append("<w:tr>" + self._cell(
                    9360,
                    f'<w:p>{_ppr(after=0, before=0, align="center")}'
                    f'{run(arrow, color=MUTED, size=20)}</w:p>') + "</w:tr>")
            rows.append("<w:tr>" + self._box(9360, lines, fill, accent) + "</w:tr>")
        self._tbl([9360], "".join(rows), borders=False)

    def fig_grid(self, cols, rows_of_boxes):
        """Fixed-column grid of boxes — for panels that are parallel, not sequential."""
        gap = 160
        box_w = (9360 - gap * (cols - 1)) // cols
        grid = []
        for i in range(cols):
            if i:
                grid.append(gap)
            grid.append(box_w)
        rows = []
        for boxes in rows_of_boxes:
            cells = []
            for i, b in enumerate(boxes):
                if i:
                    cells.append(self._cell(gap, f'<w:p>{_ppr(after=0, before=0)}</w:p>'))
                if b is None:
                    cells.append(self._cell(box_w, f'<w:p>{_ppr(after=0, before=0)}</w:p>'))
                else:
                    lines, fill, accent = b
                    cells.append(self._box(box_w, lines, fill, accent))
            rows.append("<w:tr>" + "".join(cells) + "</w:tr>")
        self._tbl(grid, "".join(rows), borders=False)

    # ---------- package ----------
    def save(self, path):
        title, creator, subject = self.meta
        sect = (
            '<w:sectPr>'
            '<w:footerReference w:type="default" r:id="rId3"/>'
            '<w:pgSz w:w="12240" w:h="15840"/>'
            '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080"'
            ' w:header="600" w:footer="600" w:gutter="0"/>'
            '<w:cols w:space="708"/><w:docGrid w:linePitch="360"/></w:sectPr>')
        doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>'
               + "".join(self.body) + sect + "</w:body></w:document>")

        styles = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
            '<w:docDefaults><w:rPrDefault><w:rPr>'
            f'<w:rFonts w:ascii="{BODY_FONT}" w:hAnsi="{BODY_FONT}" w:cs="{BODY_FONT}"/>'
            f'<w:color w:val="{INK}"/><w:sz w:val="21"/><w:szCs w:val="21"/>'
            '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
            '<w:spacing w:after="120" w:line="264" w:lineRule="auto"/>'
            '</w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            '<w:name w:val="Normal"/><w:qFormat/></w:style>'
            '<w:style w:type="table" w:default="1" w:styleId="TableNormal">'
            '<w:name w:val="Normal Table"/><w:tblPr/></w:style>'
            '</w:styles>')

        footer = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="{W}">'
            f'<w:p>{_ppr(after=0, before=0, align="right", border_bottom=None)}'
            f'{run(title + "   ·   ", color=MUTED, size=16)}'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            f'{run("1", color=MUTED, size=16, bold=True)}'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '</w:p></w:ftr>')

        settings = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<w:settings xmlns:w="{W}"><w:zoom w:percent="110"/>'
                    '<w:defaultTabStop w:val="720"/>'
                    '<w:characterSpacingControl w:val="doNotCompress"/></w:settings>')

        core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties '
                'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f'<dc:title>{escape(title)}</dc:title>'
                f'<dc:creator>{escape(creator)}</dc:creator>'
                f'<dc:subject>{escape(subject)}</dc:subject></cp:coreProperties>')

        ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<Types xmlns="{CT}">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
              '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
              '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
              '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
              '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
              '</Types>')

        pkg_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PR}">'
                    f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/>'
                    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
                    'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                    '</Relationships>')

        doc_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PR}">'
                    f'<Relationship Id="rId1" Type="{R}/styles" Target="styles.xml"/>'
                    f'<Relationship Id="rId2" Type="{R}/settings" Target="settings.xml"/>'
                    f'<Relationship Id="rId3" Type="{R}/footer" Target="footer1.xml"/>'
                    '</Relationships>')

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", ct)
            z.writestr("_rels/.rels", pkg_rels)
            z.writestr("word/document.xml", doc)
            z.writestr("word/_rels/document.xml.rels", doc_rels)
            z.writestr("word/styles.xml", styles)
            z.writestr("word/settings.xml", settings)
            z.writestr("word/footer1.xml", footer)
            z.writestr("docProps/core.xml", core)
