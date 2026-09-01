"""Сверка кассы: касса против клиентских платежей за день.

До исправления функция падала целиком: `WHERE pt.cashbox = %s`, а поля `cashbox`
у Payment Transaction нет — MariaDB отвечала «Unknown column 'pt.cashbox'».
Проверено на стенде: сверка была недоступна вообще, а не считала неточно.
"""

import unittest

import frappe

from nasiya365.api.bnpl_dashboard import get_cashbox_reconciliation

_DATE = "2026-09-01"


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_cashbox():
    return _db_insert("Cashbox", cashbox_name="Сверка " + frappe.generate_hash(length=5),
                      status="Открыта", opening_date=_DATE, opening_balance=0)


def _seed_payment(amount, methods, status="Завершен"):
    pt = _db_insert("Payment Transaction", amount=amount, payment_date=_DATE,
                    status=status, docstatus=1)
    per = amount / len(methods)
    for i, m in enumerate(methods):
        _db_insert("Payment Transaction Line", parent=pt.name,
                   parenttype="Payment Transaction", parentfield="payment_lines",
                   idx=i + 1, payment_method=m, currency="USD", amount=per)
    return pt


def _seed_cashbox_row(cashbox, ttype, method, amount, ref=None, idx=1):
    return _db_insert(
        "Cashbox Transaction", parent=cashbox, parenttype="Cashbox",
        parentfield="transactions", idx=idx, transaction_type=ttype,
        payment_method=method, currency="USD", amount=amount,
        reference_doctype="Payment Transaction" if ref else None,
        reference_name=ref,
    )


def _on_date(cashbox_row_names):
    """Проводки создаются «сейчас»; сверка смотрит на дату записи в кассу."""
    for n in cashbox_row_names:
        frappe.db.set_value("Cashbox Transaction", n, "creation", _DATE + " 10:00:00",
                            update_modified=False)


class TestCashboxReconciliation(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("cashbox_recon")

    def tearDown(self):
        frappe.db.rollback(save_point="cashbox_recon")

    def test_reconciliation_runs_at_all(self):
        # Регресс на падение: раньше здесь был OperationalError.
        cb = _seed_cashbox()
        r = get_cashbox_reconciliation(cb.name, _DATE)
        self.assertIn("ledger_totals", r)
        self.assertIn("payment_totals", r)
        self.assertIn("discrepancies", r)

    def test_matching_day_has_no_discrepancy(self):
        cb = _seed_cashbox()
        pt = _seed_payment(300, ["Наличные USD"])
        row = _seed_cashbox_row(cb.name, "Приход", "Наличные USD", 300, ref=pt.name)
        _on_date([row.name])

        r = get_cashbox_reconciliation(cb.name, _DATE)
        self.assertEqual(r["discrepancies"], [])
        self.assertAlmostEqual(r["ledger_totals"].get("Наличные USD", 0), 300, places=2)
        self.assertAlmostEqual(r["payment_totals"].get("Наличные USD", 0), 300, places=2)

    def test_expenses_do_not_offset_customer_payments(self):
        """Расход не вычитается из прихода.

        Раньше касса давала чистую сумму (приход минус расход), а платежи —
        валовую: расход в 100 выглядел как недостача в 100.
        """
        cb = _seed_cashbox()
        pt = _seed_payment(300, ["Наличные USD"])
        rows = [
            _seed_cashbox_row(cb.name, "Приход", "Наличные USD", 300, ref=pt.name, idx=1),
            _seed_cashbox_row(cb.name, "Расход", "Наличные USD", 100, idx=2),
        ]
        _on_date([r.name for r in rows])

        r = get_cashbox_reconciliation(cb.name, _DATE)
        self.assertAlmostEqual(r["ledger_totals"].get("Наличные USD", 0), 300, places=2)
        self.assertEqual(r["discrepancies"], [])

    def test_real_shortfall_is_reported(self):
        # Платёж на 300, в кассу попало 250 — расхождение должно быть видно.
        cb = _seed_cashbox()
        pt = _seed_payment(300, ["Наличные USD"])
        row = _seed_cashbox_row(cb.name, "Приход", "Наличные USD", 250, ref=pt.name)
        _on_date([row.name])

        r = get_cashbox_reconciliation(cb.name, _DATE)
        methods = [d["method"] for d in r["discrepancies"]]
        self.assertIn("Наличные USD", methods)
        self.assertFalse(r["is_balanced"])
        diff = next(d for d in r["discrepancies"] if d["method"] == "Наличные USD")
        self.assertAlmostEqual(diff["diff"], -50, places=2)  # в кассе на 50 меньше

    def test_other_cashbox_is_not_counted(self):
        cb, other = _seed_cashbox(), _seed_cashbox()
        pt = _seed_payment(300, ["Наличные USD"])
        row = _seed_cashbox_row(other.name, "Приход", "Наличные USD", 300, ref=pt.name)
        _on_date([row.name])

        r = get_cashbox_reconciliation(cb.name, _DATE)
        self.assertAlmostEqual(r["ledger_totals"].get("Наличные USD", 0), 0, places=2)
        self.assertAlmostEqual(r["payment_totals"].get("Наличные USD", 0), 0, places=2)
