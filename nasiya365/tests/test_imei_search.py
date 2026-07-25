import frappe
from frappe.tests.utils import FrappeTestCase

from nasiya365.api.bnpl_dashboard import _sanitize_imei_term, search_plans_by_imei
from nasiya365.nasiya365.report.sales_report.sales_report import execute as sales_report_execute


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


def _make_plan(imei, customer_name="Тест Клиент", status="Активный",
               product_name="iPhone 13 Test", remaining_balance=420, docstatus=0,
               start_date=None):
    """Insert a minimal Installment Plan row without running validate/hooks.

    search_plans_by_imei reads via raw SQL, so a bare row is enough and avoids
    the plan's heavy validate()/generate_schedule().
    """
    plan = frappe.get_doc({
        "doctype": "Installment Plan",
        "customer_name": customer_name,
        "imei": imei,
        "status": status,
        "product_name": product_name,
        "remaining_balance": remaining_balance,
        "docstatus": docstatus,
        "principal_amount": 1000,
        "start_date": start_date or frappe.utils.today(),
    })
    plan.name = frappe.generate_hash(length=10)
    plan.db_insert()
    return plan.name


class TestSearchPlansByImei(FrappeTestCase):
    def test_partial_match_finds_plan(self):
        name = _make_plan("356938035643809")
        found = [r["name"] for r in search_plans_by_imei("643809")]
        self.assertIn(name, found)

    def test_excludes_non_matching_plan(self):
        match = _make_plan("356938035643809")
        other = _make_plan("351756051523700")
        found = [r["name"] for r in search_plans_by_imei("643809")]
        self.assertIn(match, found)
        self.assertNotIn(other, found)

    def test_short_term_returns_empty(self):
        _make_plan("356938035643809")
        self.assertEqual(search_plans_by_imei("12"), [])

    def test_returns_expected_fields(self):
        _make_plan("356938035643809", customer_name="Иван", product_name="iPhone X")
        row = search_plans_by_imei("643809")[0]
        for key in ("name", "customer", "customer_name", "status",
                    "remaining_balance", "product_name", "imei"):
            self.assertIn(key, row)


class TestSalesReportImeiFilter(FrappeTestCase):
    def _live_plan(self, imei):
        return _make_plan(imei, docstatus=1, status="Активный",
                          start_date=frappe.utils.today())

    def test_filter_keeps_matching_plan(self):
        match = self._live_plan("356938035643809")
        other = self._live_plan("351756051523700")
        data = sales_report_execute({"imei": "643809", "sale_type": "Рассрочка"})[1]
        names = [r["doc_name"] for r in data]
        self.assertIn(match, names)
        self.assertNotIn(other, names)

    def test_column_includes_imei(self):
        name = self._live_plan("356938035643809")
        data = sales_report_execute({"imei": "643809", "sale_type": "Рассрочка"})[1]
        row = next(r for r in data if r["doc_name"] == name)
        self.assertEqual(row["imei"], "356938035643809")
