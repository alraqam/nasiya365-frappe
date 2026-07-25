import frappe
from frappe.tests.utils import FrappeTestCase

from nasiya365.api.bnpl_dashboard import _sanitize_imei_term


class TestSanitizeImeiTerm(FrappeTestCase):
    def test_keeps_full_imei(self):
        self.assertEqual(_sanitize_imei_term("356938035643809"), "356938035643809")

    def test_strips_spaces_and_letters(self):
        self.assertEqual(_sanitize_imei_term("  643 809 "), "643809")
        self.assertEqual(_sanitize_imei_term("imei:356938"), "356938")

    def test_too_short_returns_none(self):
        self.assertIsNone(_sanitize_imei_term("12"))
        self.assertIsNone(_sanitize_imei_term("ab1"))  # 1 digit

    def test_empty_and_none(self):
        self.assertIsNone(_sanitize_imei_term(""))
        self.assertIsNone(_sanitize_imei_term(None))

    def test_wildcards_are_stripped(self):
        # digits-only means LIKE metachars can never survive
        self.assertEqual(_sanitize_imei_term("35%_69\\3"), "35693")
