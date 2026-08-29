import unittest
import frappe

from nasiya365.api.profit import _compute_cash, _compute_cost_recovery


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_plan(status="Активный"):
    return _db_insert(
        "Installment Plan", imei="C5" + frappe.generate_hash(length=6),
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2030-01-01",
        status=status, contract_status="Подписан", docstatus=1,
    )


def _seed_payment(plan_name, amount, date):
    return _db_insert(
        "Payment Transaction", reference_doctype="Installment Plan",
        reference_name=plan_name, amount=amount, payment_date=date,
        status="Завершен", docstatus=1)


class TestC5WriteoffProfit(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("c5_test")

    def tearDown(self):
        frappe.db.rollback(save_point="c5_test")

    def test_writeoff_plan_excluded_from_cash(self):
        plan = _seed_plan(status="Списан")
        _seed_payment(plan.name, 350, "2030-01-10")
        r = _compute_cash("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["financed_revenue"], 0.0, places=2)  # written-off excluded
        self.assertAlmostEqual(r["financed_margin"], 0.0, places=2)

    def test_active_plan_still_counted(self):
        plan = _seed_plan(status="Активный")
        _seed_payment(plan.name, 350, "2030-01-10")
        r = _compute_cash("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["financed_revenue"], 350.0, places=2)  # active still counts

    def test_writeoff_plan_excluded_from_cost_recovery(self):
        plan = _seed_plan(status="Списан")
        _seed_payment(plan.name, 350, "2030-01-10")
        r = _compute_cost_recovery("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["financed_margin"], 0.0, places=2)  # recognized margin = 0
