import unittest
import frappe

from nasiya365.nasiya365.doctype.installment_plan.installment_plan import (
    _plan_branch_from_sources,
    _branch_decision_for_user,
)


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_branch(city="Test"):
    return _db_insert("Branch", branch_name=frappe.generate_hash(length=6), city=city)


def _seed_branch_user(branch_name, user, active=1):
    return _db_insert(
        "Branch User", parent=branch_name, parenttype="Branch",
        parentfield="branch_users", idx=1, user=user, is_active=active,
    )


def _seed_warehouse(branch_name):
    return _db_insert("Warehouse", warehouse_name=frappe.generate_hash(length=6), branch=branch_name)


def _seed_stock_entry(warehouse_name):
    return _db_insert("Stock Entry", entry_type="Поступление",
                      posting_date="2026-01-01", warehouse=warehouse_name)


def _seed_sales_order(branch_name):
    return _db_insert("Sales Order", total_amount=1000, order_date="2026-01-01",
                      branch=branch_name, docstatus=1)


def _plan(**fields):
    """In-memory Installment Plan (не сохраняется) для прямого вызова _autoset_branch."""
    return frappe.get_doc({"doctype": "Installment Plan", **fields})


class TestPlanBranchAutoset(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("plan_branch_test")

    def tearDown(self):
        frappe.db.rollback(save_point="plan_branch_test")

    # ── _plan_branch_from_sources ────────────────────
    def test_source_from_sales_order(self):
        b = _seed_branch()
        so = _seed_sales_order(b.name)
        self.assertEqual(_plan_branch_from_sources(so.name, None), b.name)

    def test_source_from_stock_entry_warehouse(self):
        b = _seed_branch()
        wh = _seed_warehouse(b.name)
        se = _seed_stock_entry(wh.name)
        self.assertEqual(_plan_branch_from_sources(None, se.name), b.name)

    def test_source_sales_order_wins_over_stock_entry(self):
        b1 = _seed_branch(); b2 = _seed_branch()
        so = _seed_sales_order(b1.name)
        wh = _seed_warehouse(b2.name); se = _seed_stock_entry(wh.name)
        self.assertEqual(_plan_branch_from_sources(so.name, se.name), b1.name)

    def test_source_none_when_no_refs(self):
        self.assertIsNone(_plan_branch_from_sources(None, None))

    # ── _branch_decision_for_user (явный user, без set_user) ──
    def test_decision_single_branch_ok(self):
        b = _seed_branch()
        user = "op_single_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(b.name, user)
        self.assertEqual(_branch_decision_for_user(user), ("ok", b.name))

    def test_decision_multi_branch_ambiguous(self):
        b1 = _seed_branch(); b2 = _seed_branch()
        user = "op_multi_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(b1.name, user)
        _seed_branch_user(b2.name, user)
        self.assertEqual(_branch_decision_for_user(user), ("ambiguous", None))

    def test_decision_no_branch_skips(self):
        user = "op_none_%s@test.local" % frappe.generate_hash(length=4)
        self.assertEqual(_branch_decision_for_user(user), ("skip", None))

    def test_decision_unrestricted_skips(self):
        self.assertEqual(_branch_decision_for_user("Administrator"), ("skip", None))

    # ── _autoset_branch (оркестрация) ────────────────
    def test_autoset_keeps_manual_branch(self):
        b1 = _seed_branch(); b2 = _seed_branch()
        so = _seed_sales_order(b2.name)
        p = _plan(branch=b1.name, sales_order=so.name)
        p._autoset_branch()
        self.assertEqual(p.branch, b1.name)   # ручной не перезаписан

    def test_autoset_from_sales_order(self):
        b = _seed_branch()
        so = _seed_sales_order(b.name)
        p = _plan(sales_order=so.name)
        p._autoset_branch()
        self.assertEqual(p.branch, b.name)

    def test_autoset_ambiguous_throws(self):
        # decision-функцию мокаем (session.user в тестах = Administrator/unrestricted,
        # поэтому throw-ветку изолируем от привязок пользователя)
        import nasiya365.nasiya365.doctype.installment_plan.installment_plan as ip_mod
        orig = ip_mod._branch_decision_for_user
        ip_mod._branch_decision_for_user = lambda u: ("ambiguous", None)
        try:
            p = _plan()   # без sales_order/stock_entry/branch
            with self.assertRaises(frappe.exceptions.ValidationError):
                p._autoset_branch()
        finally:
            ip_mod._branch_decision_for_user = orig

    def test_autoset_skip_leaves_empty(self):
        import nasiya365.nasiya365.doctype.installment_plan.installment_plan as ip_mod
        orig = ip_mod._branch_decision_for_user
        ip_mod._branch_decision_for_user = lambda u: ("skip", None)
        try:
            p = _plan()
            p._autoset_branch()
            self.assertFalse(p.get("branch"))
        finally:
            ip_mod._branch_decision_for_user = orig


def _seed_plan(branch=None, sales_order=None):
    return _db_insert(
        "Installment Plan", imei="IMEI" + frappe.generate_hash(length=6),
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2026-01-01",
        status="Активный", contract_status="Активный", docstatus=1,
        branch=branch, sales_order=sales_order,
    )


class TestPlanBranchVisibility(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("plan_branch_vis")

    def tearDown(self):
        frappe.db.rollback(save_point="plan_branch_vis")

    def _visible(self, plan_name, user):
        from nasiya365.api.bnpl_dashboard import _user_branch_clause
        frappe.cache().delete_value("nasiya365:user_branches:%s" % user)
        frag, params = _user_branch_clause("ip", user=user)
        rows = frappe.db.sql(
            f"SELECT ip.name FROM `tabInstallment Plan` ip WHERE ip.name = %s {frag}",
            (plan_name, *params),
        )
        frappe.cache().delete_value("nasiya365:user_branches:%s" % user)
        return bool(rows)

    def test_new_plan_direct_branch_visible_to_its_branch(self):
        a = _seed_branch()
        user = "op_a_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(a.name, user)
        p = _seed_plan(branch=a.name, sales_order=None)   # новый: своё поле, без SO
        self.assertTrue(self._visible(p.name, user))

    def test_new_plan_not_visible_to_other_branch(self):
        a = _seed_branch(); other = _seed_branch()
        user_other = "op_o_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(other.name, user_other)
        p = _seed_plan(branch=a.name, sales_order=None)
        self.assertFalse(self._visible(p.name, user_other))

    def test_legacy_plan_via_sales_order_still_visible(self):
        a = _seed_branch()
        user = "op_a2_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(a.name, user)
        so = _seed_sales_order(a.name)
        p = _seed_plan(branch=None, sales_order=so.name)   # старый: branch пуст, SO→A
        self.assertTrue(self._visible(p.name, user))       # fallback работает

    def test_payment_branch_uses_direct_field(self):
        # платёж по новому плану (branch=A, без SO) должен относиться к филиалу A
        a = _seed_branch()
        p = _seed_plan(branch=a.name, sales_order=None)
        _db_insert("Payment Transaction", reference_doctype="Installment Plan",
                   reference_name=p.name, amount=100, status="Завершен",
                   payment_date="2026-06-01", docstatus=1)
        from nasiya365.api.profit import _PAYMENT_BRANCH_CASE
        row = frappe.db.sql(
            f"SELECT ({_PAYMENT_BRANCH_CASE}) AS branch FROM `tabPayment Transaction` pt "
            f"WHERE pt.reference_name = %s AND pt.reference_doctype = 'Installment Plan'",
            (p.name,),
        )
        self.assertEqual(row[0][0], a.name)
