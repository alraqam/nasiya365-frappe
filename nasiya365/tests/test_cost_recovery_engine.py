import frappe
from frappe.tests.utils import FrappeTestCase

from nasiya365.api.profit import _compute_cost_recovery


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


class TestCostRecoveryEngine(FrappeTestCase):
    """Seeds one financed deal + payment directly via db_insert (see _db_insert),
    inside FrappeTestCase's per-test transaction (auto rolled back)."""

    def _seed_plan_with_payment(self, principal, financed, interest, cogs, pay_amount, pay_date):
        # Minimal Stock Entry + item so COGS resolves by IMEI.
        imei = "TESTIMEI000001"
        se = _db_insert(
            "Stock Entry",
            entry_type="Поступление",
            posting_date=pay_date,
        )
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

        plan = _db_insert(
            "Installment Plan",
            imei=imei,
            principal_amount=principal,
            financed_amount=financed,
            total_interest=interest,
            total_amount=principal + interest,
            start_date=pay_date,
            status="Активный",
            contract_status="Активный",
            docstatus=1,
        )

        _db_insert(
            "Payment Transaction",
            reference_doctype="Installment Plan",
            reference_name=plan.name,
            amount=pay_amount,
            status="Завершен",
            payment_date=pay_date,
            docstatus=0,
        )
        return plan

    def test_down_payment_below_cost_recognizes_zero(self):
        # phone: principal 650, cogs 620, interest 90 -> total profit 120
        # down 350 collected today < 620 -> recognized 0
        d = frappe.utils.today()
        self._seed_plan_with_payment(650, 300, 90, 620, 350, d)
        comp = _compute_cost_recovery(d, d, None)
        self.assertAlmostEqual(comp["financed_margin"], 0.0, places=2)
        self.assertAlmostEqual(comp["interest_income"], 0.0, places=2)
        self.assertAlmostEqual(comp["collected"], 350.0, places=2)
        # Раздел 1 still shows the sale's potential
        self.assertAlmostEqual(comp["sales_financed_margin"], 30.0, places=2)
        self.assertAlmostEqual(comp["potential_profit"], 120.0, places=2)
