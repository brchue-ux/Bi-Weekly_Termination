"""Domain primitives and the input parsing that feeds them.

Every case here corresponds to a way the pipeline previously produced a silently wrong
answer rather than an error.
"""
import datetime as dt
import io
import unittest
import zipfile

import tests  # noqa: F401

import biterm_domain as domain
import xlsx_min


class ExpiryParsing(unittest.TestCase):
    """A lapsed exception that reads as valid is a direct control failure."""

    def test_iso_date(self):
        self.assertEqual(domain.parse_date("2026-12-31"), dt.date(2026, 12, 31))

    def test_excel_serial_is_a_real_date_not_a_string(self):
        # The bug: "46023" < "2026-07-26" is False as a string compare, so this exception
        # never expired. 46023 is 2026-01-01.
        self.assertEqual(domain.parse_date("46023"), dt.date(2026, 1, 1))
        self.assertTrue(domain.is_expired(domain.parse_date("46023"), dt.date(2026, 7, 26)))

    def test_excel_serial_epoch_anchor(self):
        self.assertEqual(domain.parse_date("1"), dt.date(1899, 12, 31))
        self.assertEqual(domain.parse_date("45000"), dt.date(2023, 3, 15))

    def test_unambiguous_slash_dates_are_accepted(self):
        self.assertEqual(domain.parse_date("31/12/2026"), dt.date(2026, 12, 31))
        self.assertEqual(domain.parse_date("12/31/2026"), dt.date(2026, 12, 31))

    def test_ambiguous_slash_date_is_rejected_not_guessed(self):
        with self.assertRaises(domain.DateFormatError):
            domain.parse_date("01/12/2026")

    def test_blank_is_rejected(self):
        for value in ("", "   ", None):
            with self.assertRaises(domain.DateFormatError):
                domain.parse_date(value)

    def test_garbage_is_rejected(self):
        with self.assertRaises(domain.DateFormatError):
            domain.parse_date("see owner")

    def test_expiry_boundary(self):
        today = dt.date(2026, 7, 26)
        self.assertFalse(domain.is_expired(dt.date(2026, 7, 26), today))
        self.assertTrue(domain.is_expired(dt.date(2026, 7, 25), today))


class Privilege(unittest.TestCase):
    """Rule: privilege can never be hidden behind a lower role."""

    def test_highest_privilege_wins(self):
        self.assertEqual(
            domain.highest_privilege({"Power User", "Administrator", "Read Only"}),
            "Administrator")

    def test_single_role_is_returned(self):
        self.assertEqual(domain.highest_privilege(["Read Only"]), "Read Only")

    def test_unranked_role_raises_instead_of_sorting_lowest(self):
        with self.assertRaises(domain.UnknownRoleError):
            domain.highest_privilege({"Administrator", "Wizard"})

    def test_result_does_not_depend_on_iteration_order(self):
        roles = ["Administrator", "Power User", "Standard User", "Read Only"]
        results = {domain.highest_privilege(set(roles[i:] + roles[:i]))
                   for i in range(len(roles))}
        self.assertEqual(results, {"Administrator"})

    def test_winner_is_by_privilege_not_alphabetical(self):
        """Caught by the mutation pass: asserting only {Administrator, …} -> Administrator
        also passes when the whole ranking is flattened, because Administrator happens to
        sort first alphabetically. This pair disagrees — "Read Only" sorts first but
        "Standard User" is the more privileged."""
        self.assertEqual(domain.highest_privilege({"Read Only", "Standard User"}),
                         "Standard User")
        self.assertEqual(domain.highest_privilege({"Read Only", "Service Account"}),
                         "Read Only")

    def test_ranking_is_strictly_ordered(self):
        order = ["Service Account", "Read Only", "Standard User", "Power User", "Administrator"]
        ranks = [domain.PRIVILEGE_ORDER[r] for r in order]
        self.assertEqual(ranks, sorted(ranks), "the ranking must be monotonic")
        self.assertEqual(len(set(ranks)), len(ranks), "no two roles may share a rank")


class IdentityKeys(unittest.TestCase):
    def test_account_id_is_preferred_over_upn(self):
        r = {"alias": "acct1", "upn": "a@wm.com", "empid": "E1"}
        self.assertEqual(domain.identity_key(r), "acct:acct1")

    def test_key_is_stable_when_the_upn_is_backfilled(self):
        before = {"alias": "acct1", "upn": "", "empid": ""}
        after = {"alias": "acct1", "upn": "found@wm.com", "empid": "E1"}
        self.assertEqual(domain.identity_key(before), domain.identity_key(after))

    def test_legacy_key_reproduces_the_old_formula(self):
        self.assertEqual(
            domain.legacy_identity_key({"alias": "acct1", "upn": ""}), "alias:acct1")
        self.assertEqual(
            domain.legacy_identity_key({"alias": "acct1", "upn": "a@wm.com"}), "a@wm.com")

    def test_rows_with_no_identifier_still_get_a_distinct_key(self):
        a = domain.identity_key({"alias": "", "upn": "", "empid": "", "src": {"x": "1"}})
        b = domain.identity_key({"alias": "", "upn": "", "empid": "", "src": {"x": "2"}})
        self.assertNotEqual(a, b)


class LoginNormalisation(unittest.TestCase):
    def test_sentinel_is_not_a_login(self):
        self.assertEqual(domain.normalise_upn(domain.NO_UPN_SENTINEL), "")

    def test_spaces_and_missing_at_are_rejected(self):
        self.assertEqual(domain.normalise_upn("no upn"), "")
        self.assertEqual(domain.normalise_upn("nobody"), "")

    def test_case_is_normalised(self):
        self.assertEqual(domain.normalise_upn("  A.Person@WM.com "), "a.person@wm.com")


def _make_xlsx(rows_xml, styles=None, shared=None):
    """Build a minimal single-sheet workbook in memory."""
    buf = io.BytesIO()
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" '
                   'ContentType="application/xml"/></Types>')
        z.writestr("xl/workbook.xml",
                   f'<?xml version="1.0"?><workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                   f'<sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.'
                   'org/package/2006/relationships"><Relationship Id="rId1" '
                   f'Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<?xml version="1.0"?><worksheet xmlns="{main}"><sheetData>{rows_xml}'
                   f'</sheetData></worksheet>')
        if styles:
            z.writestr("xl/styles.xml", styles)
        if shared:
            z.writestr("xl/sharedStrings.xml", shared)
    buf.seek(0)
    return buf


class XlsxReading(unittest.TestCase):
    def test_omitted_rows_do_not_shift_later_rows(self):
        """Excel omits empty rows. Appending in document order silently mapped every
        subsequent row — including the header — one position too early."""
        xml = ('<row r="1"><c r="A1" t="inlineStr"><is><t>title</t></is></c></row>'
               '<row r="3"><c r="A3" t="inlineStr"><is><t>TH_UPN</t></is></c></row>'
               '<row r="4"><c r="A4" t="inlineStr"><is><t>a@wm.com</t></is></c></row>')
        rows = xlsx_min.load_workbook_rows(_make_xlsx(xml))["Sheet1"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1], {}, "the omitted row 2 must be materialised as empty")
        self.assertEqual(rows[2][0], "TH_UPN")

    def test_header_is_found_by_content_not_by_index(self):
        xml = ('<row r="1"><c r="A1" t="inlineStr"><is><t>report</t></is></c></row>'
               '<row r="2"></row>'
               '<row r="3"><c r="A3" t="inlineStr"><is><t>TH_UPN</t></is></c>'
               '<c r="B3" t="inlineStr"><is><t>TH_EmployeeStatus</t></is></c></row>')
        rows = xlsx_min.load_workbook_rows(_make_xlsx(xml))["Sheet1"]
        idx, headers = xlsx_min.find_header_row(rows, ["TH_UPN", "TH_EmployeeStatus"])
        self.assertEqual(idx, 2)
        self.assertEqual(xlsx_min.column(headers, "TH_UPN"), 0)

    def test_missing_header_raises_and_names_what_it_looked_for(self):
        xml = '<row r="1"><c r="A1" t="inlineStr"><is><t>nope</t></is></c></row>'
        rows = xlsx_min.load_workbook_rows(_make_xlsx(xml))["Sheet1"]
        with self.assertRaises(xlsx_min.XlsxError) as cm:
            xlsx_min.find_header_row(rows, ["TH_UPN"], sheet_name="NA Orion")
        self.assertIn("TH_UPN", str(cm.exception))
        self.assertIn("NA Orion", str(cm.exception))

    def test_date_formatted_cell_is_returned_as_a_date_not_a_serial(self):
        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        styles = (f'<?xml version="1.0"?><styleSheet xmlns="{main}"><cellXfs count="2">'
                  '<xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
        xml = '<row r="1"><c r="A1" s="1"><v>46023</v></c><c r="B1" s="0"><v>46023</v></c></row>'
        rows = xlsx_min.load_workbook_rows(_make_xlsx(xml, styles=styles))["Sheet1"]
        self.assertEqual(rows[0][0], "2026-01-01", "date-formatted cell must resolve")
        self.assertEqual(rows[0][1], "46023", "a plain number must stay a number")

    def test_ambiguous_alias_column_is_rejected(self):
        headers = {"A_NetworkAlias": 0, "B_NetworkAlias": 1}
        with self.assertRaises(xlsx_min.XlsxError):
            xlsx_min.column_by_suffix(headers, ("_NetworkAlias",), sheet_name="NA Orion")

    def test_corrupt_shared_string_index_raises(self):
        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        shared = f'<?xml version="1.0"?><sst xmlns="{main}"><si><t>only</t></si></sst>'
        xml = '<row r="1"><c r="A1" t="s"><v>9</v></c></row>'
        with self.assertRaises(xlsx_min.XlsxError):
            xlsx_min.load_workbook_rows(_make_xlsx(xml, shared=shared))


if __name__ == "__main__":
    unittest.main()
