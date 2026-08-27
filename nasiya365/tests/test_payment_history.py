import unittest
import frappe

from nasiya365.nasiya365.doctype.installment_plan.installment_plan import (
    get_payment_history,
)


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_plan():
    return _db_insert(
        "Installment Plan", imei="PHIST" + frappe.generate_hash(length=5),
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2026-01-01",
        status="Активный", contract_status="Активный", docstatus=1,
    )


def _seed_payment(plan_name, amount, date, methods, docstatus=1, status="Завершен"):
    pt = _db_insert(
        "Payment Transaction", reference_doctype="Installment Plan",
        reference_name=plan_name, amount=amount, payment_date=date,
        status=status, docstatus=docstatus,
    )
    per = amount / len(methods) if methods else 0
    for i, m in enumerate(methods):
        _db_insert(
            "Payment Transaction Line", parent=pt.name,
            parenttype="Payment Transaction", parentfield="payment_lines",
            idx=i + 1, payment_method=m, amount=per,
        )
    return pt


class TestPaymentHistory(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("pay_hist_test")

    def tearDown(self):
        frappe.db.rollback(save_point="pay_hist_test")

    def test_three_payments_sorted_and_totalled(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-20", ["Карта"])
        _seed_payment(plan.name, 60, "2026-09-10", ["Наличные USD"])
        _seed_payment(plan.name, 80, "2026-09-28", ["Карта"])
        r = get_payment_history(plan.name)
        self.assertEqual(r["count"], 3)
        self.assertEqual([p["payment_date"] for p in r["payments"]],
                         ["2026-09-10", "2026-09-20", "2026-09-28"])  # sorted asc
        self.assertAlmostEqual(r["total"], 240.0, places=2)

    def test_single_method_shown(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-01", ["Карта"])
        r = get_payment_history(plan.name)
        self.assertEqual(r["payments"][0]["method"], "Карта")

    def test_combined_method(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-01", ["Карта", "Наличные USD"])
        r = get_payment_history(plan.name)
        self.assertEqual(r["payments"][0]["method"], "Комбинированный")

    def test_cancelled_and_draft_excluded(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-01", ["Карта"])                 # ok
        _seed_payment(plan.name, 50, "2026-09-02", ["Карта"], docstatus=2)     # cancelled
        _seed_payment(plan.name, 50, "2026-09-03", ["Карта"], docstatus=0,
                      status="Ожидает")                                        # draft
        r = get_payment_history(plan.name)
        self.assertEqual(r["count"], 1)
        self.assertAlmostEqual(r["total"], 100.0, places=2)

    def test_empty_plan(self):
        plan = _seed_plan()
        r = get_payment_history(plan.name)
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["payments"], [])
        self.assertEqual(r["total"], 0)

    def test_submitted_but_not_completed_excluded(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-01", ["Карта"])                          # docstatus=1, Завершен → ok
        _seed_payment(plan.name, 50, "2026-09-02", ["Карта"], docstatus=1,
                      status="Ожидает")                                                 # docstatus=1 but NOT Завершен → excluded
        r = get_payment_history(plan.name)
        self.assertEqual(r["count"], 1)
        self.assertAlmostEqual(r["total"], 100.0, places=2)
