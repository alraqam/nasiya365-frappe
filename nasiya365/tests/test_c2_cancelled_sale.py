import unittest
import frappe

from nasiya365.api.profit import _compute_cash


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_sales_order(total, order_date, docstatus=1, branch=None):
    return _db_insert("Sales Order", total_amount=total, order_date=order_date,
                      branch=branch, docstatus=docstatus)


def _seed_payment(rdt, rn, amount, date, docstatus=1, status="Завершен"):
    return _db_insert("Payment Transaction", reference_doctype=rdt, reference_name=rn,
                      amount=amount, payment_date=date, status=status, docstatus=docstatus)


class TestC2CancelledSale(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("c2_test")

    def tearDown(self):
        frappe.db.rollback(save_point="c2_test")

    def test_cancelled_so_excluded_from_cash(self):
        # both have a live payment in the window; only the docstatus=1 SO counts
        ok = _seed_sales_order(800, "2030-01-05", docstatus=1)
        cancelled = _seed_sales_order(800, "2030-01-06", docstatus=2)
        _seed_payment("Sales Order", ok.name, 800, "2030-01-10")
        _seed_payment("Sales Order", cancelled.name, 800, "2030-01-11")
        r = _compute_cash("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["cash_revenue"], 800.0, places=2)  # cancelled one excluded

    def test_normal_so_still_counted(self):
        ok = _seed_sales_order(500, "2030-01-05", docstatus=1)
        _seed_payment("Sales Order", ok.name, 500, "2030-01-10")
        r = _compute_cash("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["cash_revenue"], 500.0, places=2)

    def test_cost_recovery_excludes_cancelled_so(self):
        from nasiya365.api.profit import _compute_cost_recovery
        cancelled = _seed_sales_order(800, "2030-01-06", docstatus=2)
        _seed_payment("Sales Order", cancelled.name, 800, "2030-01-11")
        r = _compute_cost_recovery("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["cash_margin"], 0.0, places=2)  # recognized margin from cancelled = 0

    def test_cancel_linked_cash_receipt(self):
        so = _seed_sales_order(800, "2030-01-05", docstatus=1)
        pt = _seed_payment("Sales Order", so.name, 800, "2030-01-05")   # docstatus=1
        frappe.get_doc("Sales Order", so.name)._cancel_linked_cash_receipt()
        self.assertEqual(frappe.db.get_value("Payment Transaction", pt.name, "docstatus"), 2)

    def test_cancel_receipt_idempotent_no_payment(self):
        so = _seed_sales_order(800, "2030-01-05", docstatus=1)          # нет платежа
        frappe.get_doc("Sales Order", so.name)._cancel_linked_cash_receipt()   # без ошибки

    def test_cancel_receipt_skips_already_cancelled(self):
        so = _seed_sales_order(800, "2030-01-05", docstatus=1)
        _seed_payment("Sales Order", so.name, 800, "2030-01-05", docstatus=2, status="Отменен")
        frappe.get_doc("Sales Order", so.name)._cancel_linked_cash_receipt()   # без ошибки (нечего отменять)
