import unittest

import frappe

from nasiya365.api.profit import _compute_cost_recovery

# All new-in-this-file scenarios use dates in this "safe" year, well outside the
# range of the dev site's pre-existing real demo data (Installment Plan /
# Payment Transaction / Sales Order rows on this shared dev DB top out around
# 2026-07-25 -- see the git history / task-3-report.md for how this was
# confirmed). Using calendar dates that can't collide with real rows avoids the
# aggregate SUM()s in _compute_cost_recovery's SQL picking up unrelated data and
# breaking the exact-value assertions below. The relative structure (a payment a
# month before the period, one inside it) is what matters for the boundary-
# crossing math, not the specific calendar year.
_SAFE_YEAR = "2031"


def _db_insert(doctype, **fields):
    """Insert a minimal row bypassing validate()/hooks, matching the pattern already
    used in test_imei_search.py: db_insert() writes only the parent row's own
    columns (with valid values), running no controller side effects. Suitable here
    because we're exercising `_compute_cost_recovery`'s SQL/reads, not the doctype
    controllers (Installment Plan.validate() would otherwise recompute
    financed_amount/total_interest/total_amount from scratch and require a real
    Customer Profile; Payment Transaction.validate() requires payment_lines rows
    that our SQL-based reads never touch)."""
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_stock(imei, cogs, posting_date):
    """Minimal Stock Entry + item so COGS resolves by IMEI."""
    se = _db_insert("Stock Entry", entry_type="Поступление", posting_date=posting_date)
    _db_insert(
        "Stock Entry Item",
        parent=se.name,
        parenttype="Stock Entry",
        parentfield="items",
        idx=1,
        imei=imei,
        quantity=1,
        rate=cogs,
        amount=cogs,
        expense=0,
    )
    return se


def _seed_plan(imei, principal, financed, interest, start_date, sales_order=None):
    return _db_insert(
        "Installment Plan",
        imei=imei,
        principal_amount=principal,
        financed_amount=financed,
        total_interest=interest,
        total_amount=principal + interest,
        start_date=start_date,
        status="Активный",
        contract_status="Активный",
        docstatus=1,
        sales_order=sales_order,
    )


def _seed_payment(reference_doctype, reference_name, amount, payment_date):
    return _db_insert(
        "Payment Transaction",
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        amount=amount,
        status="Завершен",
        payment_date=payment_date,
        docstatus=0,
    )


def _seed_sales_order(total_amount, order_date, branch=None):
    return _db_insert(
        "Sales Order",
        total_amount=total_amount,
        order_date=order_date,
        branch=branch,
        docstatus=1,
    )


def _seed_sales_order_item(so_name, imei):
    return _db_insert(
        "Sales Order Item",
        parent=so_name,
        parenttype="Sales Order",
        parentfield="items",
        idx=1,
        imei=imei,
        quantity=1,
    )


def _seed_branch(name):
    return _db_insert("Branch", name=name, branch_name=name, city="Test")


class TestCostRecoveryEngine(unittest.TestCase):
    """Seeds deals + payments directly via db_insert (see _db_insert helpers above).

    Uses a per-test SAVEPOINT (setUp/tearDown), NOT FrappeTestCase and NOT a plain
    frappe.db.begin()/rollback(): FrappeTestCase only rolls back once at class
    teardown (frappe.db.begin()/commit() happen in setUpClass, rollback is
    registered via addClassCleanup), so all test methods in a class share one
    uncommitted transaction -- several scenarios below use overlapping "safe"
    calendar dates on purpose (to exercise period-boundary crossing), and under
    FrappeTestCase's class-wide transaction one test's seeded rows were still
    visible to the next test's SUM() queries, inflating the aggregates and
    breaking exact-value assertions. A plain `frappe.db.begin()` doesn't work
    either: this Frappe version raises `ImplicitCommitError` on a bare
    `START TRANSACTION` issued while already inside a transaction (which the test
    runner itself starts). SAVEPOINT/ROLLBACK TO SAVEPOINT (the same mechanism
    already used elsewhere in this app, e.g. payment_transaction.py's
    `frappe.db.savepoint("nasiya_payment_on_submit")`) nests cleanly inside the
    existing transaction and gives true per-test isolation without conflicting
    with it.
    """

    def setUp(self):
        frappe.db.savepoint("cost_recovery_test")

    def tearDown(self):
        frappe.db.rollback(save_point="cost_recovery_test")

    def _seed_plan_with_payment(self, principal, financed, interest, cogs, pay_amount, pay_date):
        imei = "TESTIMEI000001"
        _seed_stock(imei, cogs, pay_date)
        plan = _seed_plan(imei, principal, financed, interest, pay_date)
        _seed_payment("Installment Plan", plan.name, pay_amount, pay_date)
        return plan

    def test_down_payment_below_cost_recognizes_zero(self):
        # phone: principal 650, cogs 620, interest 90 -> total profit 120
        # down 350 collected < 620 -> recognized 0
        d = f"{_SAFE_YEAR}-05-25"
        self._seed_plan_with_payment(650, 300, 90, 620, 350, d)
        comp = _compute_cost_recovery(d, d, None)
        self.assertAlmostEqual(comp["financed_margin"], 0.0, places=2)
        self.assertAlmostEqual(comp["interest_income"], 0.0, places=2)
        self.assertAlmostEqual(comp["collected"], 350.0, places=2)
        # Раздел 1 still shows the sale's potential
        self.assertAlmostEqual(comp["sales_financed_margin"], 30.0, places=2)
        self.assertAlmostEqual(comp["potential_profit"], 120.0, places=2)

    def test_boundary_crossing_recognizes_on_second_period(self):
        # Same deal as above (650 / 300 fin / 90 int / cogs 620 -> total profit 120),
        # but the down payment (350, below cost) lands in period 1 and the second
        # payment (400) crosses the cost-recovery boundary (before=350, after=750)
        # in period 2, recognizing the full 120 (30 margin + 90 interest) there.
        imei = "TESTIMEI000002"
        pay1_date = f"{_SAFE_YEAR}-06-25"
        pay2_date = f"{_SAFE_YEAR}-07-25"
        _seed_stock(imei, 620, pay1_date)
        plan = _seed_plan(imei, 650, 300, 90, pay1_date)
        _seed_payment("Installment Plan", plan.name, 350, pay1_date)
        _seed_payment("Installment Plan", plan.name, 400, pay2_date)

        # Period 1: only the 350 down payment is in-window -> still below cost.
        comp1 = _compute_cost_recovery(pay1_date, pay1_date, None)
        self.assertAlmostEqual(comp1["financed_margin"], 0.0, places=2)
        self.assertAlmostEqual(comp1["interest_income"], 0.0, places=2)
        self.assertAlmostEqual(comp1["collected"], 350.0, places=2)

        # Period 2: before=350, after=750 -> crosses cost (620), recognizes all 120.
        comp2 = _compute_cost_recovery(pay2_date, pay2_date, None)
        self.assertAlmostEqual(comp2["financed_margin"], 30.0, places=2)
        self.assertAlmostEqual(comp2["interest_income"], 90.0, places=2)
        self.assertAlmostEqual(comp2["collected"], 400.0, places=2)

        # Whole window: both payments in one period -> same total recognized (120).
        comp_all = _compute_cost_recovery(f"{_SAFE_YEAR}-06-01", f"{_SAFE_YEAR}-07-31", None)
        self.assertAlmostEqual(comp_all["financed_margin"], 30.0, places=2)
        self.assertAlmostEqual(comp_all["interest_income"], 90.0, places=2)
        self.assertAlmostEqual(comp_all["collected"], 750.0, places=2)

    def test_cash_sales_order_recognizes_same_day(self):
        # Cash sale (no Installment Plan): total 650, cogs 620 -> margin 30, no
        # interest. Paid in full same day -> recognized immediately (delta = 30).
        imei = "TESTIMEI000003"
        d = f"{_SAFE_YEAR}-07-25"
        _seed_stock(imei, 620, d)
        so = _seed_sales_order(650, d)
        _seed_sales_order_item(so.name, imei)
        _seed_payment("Sales Order", so.name, 650, d)

        comp = _compute_cost_recovery(d, d, None)
        self.assertAlmostEqual(comp["cash_margin"], 30.0, places=2)
        self.assertAlmostEqual(comp["interest_income"], 0.0, places=2)
        self.assertAlmostEqual(comp["collected"], 650.0, places=2)

    def test_branch_filter_excludes_other_branch(self):
        # Deal's branch is resolved via its Sales Order (plan.sales_order -> so.branch).
        imei = "TESTIMEI000004"
        d = f"{_SAFE_YEAR}-08-25"
        # _db_insert always assigns the row's real primary key via generate_hash
        # (see _db_insert docstring) -- branch_name/city are just data fields, so
        # the branch filter passed to _compute_cost_recovery must use the actual
        # generated `.name`, not the human-readable branch_name text.
        branch_a = _seed_branch("BR-Test-CostRecoveryA")
        branch_b = _seed_branch("BR-Test-CostRecoveryB")  # a real, distinct branch to filter by
        so = _seed_sales_order(650, d, branch=branch_a.name)
        _seed_stock(imei, 620, d)
        plan = _seed_plan(imei, 650, 300, 90, d, sales_order=so.name)
        _seed_payment("Installment Plan", plan.name, 350, d)

        # Wrong branch -> excluded entirely.
        excluded = _compute_cost_recovery(d, d, branch_b.name)
        self.assertAlmostEqual(excluded["financed_margin"], 0.0, places=2)
        self.assertAlmostEqual(excluded["collected"], 0.0, places=2)

        # Correct branch -> included (350 collected, still below the 620 cost -> 0 recognized).
        included = _compute_cost_recovery(d, d, branch_a.name)
        self.assertAlmostEqual(included["financed_margin"], 0.0, places=2)
        self.assertAlmostEqual(included["collected"], 350.0, places=2)

        # Unrestricted (branch=None, admin in this test run) -> also included.
        unrestricted = _compute_cost_recovery(d, d, None)
        self.assertAlmostEqual(unrestricted["collected"], 350.0, places=2)
