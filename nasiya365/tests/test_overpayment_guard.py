import unittest
import frappe

from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
    _resolve_installment_plan_name,
    _installment_plan_remaining,
)


def _db_insert(doctype, **fields):
    """Вставка строки в обход validate()/hooks (паттерн test_cost_recovery_engine)."""
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _plan(remaining_balance=0, sales_order=None):
    return _db_insert(
        "Installment Plan",
        imei="TESTIMEI0001",
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2026-01-01",
        status="Активный", contract_status="Активный", docstatus=1,
        remaining_balance=remaining_balance, sales_order=sales_order,
    )


def _sched_row(plan_name, idx, amount, paid_amount):
    return _db_insert(
        "Installment Schedule",
        parent=plan_name, parenttype="Installment Plan", parentfield="schedule",
        idx=idx, installment_number=idx, amount=amount, paid_amount=paid_amount,
        due_date="2026-02-01", status="Ожидает",
    )


def _pt(reference_doctype, reference_name, amount):
    """In-memory Payment Transaction (без сохранения) для прямого вызова методов гарда."""
    return frappe.get_doc({
        "doctype": "Payment Transaction",
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "amount": amount,
        "payment_date": "2026-08-01",
    })


class TestOverpaymentGuard(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("nasiya_overpay_test")

    def tearDown(self):
        frappe.db.rollback(save_point="nasiya_overpay_test")

    # ── Task 1: resolver ─────────────────────────────
    def test_resolve_installment_plan_reference(self):
        plan = _plan(remaining_balance=100)
        self.assertEqual(_resolve_installment_plan_name(_pt("Installment Plan", plan.name, 1)), plan.name)

    def test_resolve_via_sales_order(self):
        so = _db_insert("Sales Order", total_amount=1200, order_date="2026-01-01", docstatus=1)
        plan = _plan(remaining_balance=100, sales_order=so.name)
        self.assertEqual(_resolve_installment_plan_name(_pt("Sales Order", so.name, 1)), plan.name)

    def test_resolve_none_for_cash_sales_order(self):
        so = _db_insert("Sales Order", total_amount=650, order_date="2026-01-01", docstatus=1)
        self.assertIsNone(_resolve_installment_plan_name(_pt("Sales Order", so.name, 1)))

    # ── Task 2: remaining ────────────────────────────
    def test_remaining_uses_header(self):
        plan = _plan(remaining_balance=165.91)
        self.assertAlmostEqual(_installment_plan_remaining(plan.name), 165.91, places=2)

    def test_remaining_falls_back_to_schedule_when_header_zero(self):
        plan = _plan(remaining_balance=0)
        _sched_row(plan.name, 1, amount=100, paid_amount=60)   # due = 40
        self.assertAlmostEqual(_installment_plan_remaining(plan.name), 40.0, places=2)

    def test_remaining_zero_for_closed_plan(self):
        plan = _plan(remaining_balance=0)                       # нет строк графика
        self.assertEqual(_installment_plan_remaining(plan.name), 0.0)
