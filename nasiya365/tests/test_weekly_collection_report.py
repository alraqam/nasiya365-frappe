"""Недельный отчёт по сборам.

До переписывания это была заглушка: период 8 календарных дат вместо 7,
просрочка считалась как SUM(amount) без вычета оплаченного, «собрано» включало
любые платежи за неделю — наличные продажи, авансы и погашение старой просрочки,
— а эффективность делила одно на другое.
"""

import unittest

import frappe
from frappe.utils import add_days

from nasiya365.tasks.weekly import generate_collection_report

_END = "2026-09-07"          # конец периода
_IN_FIRST = "2026-09-01"     # первая дата периода (end − 6)
_OUT = "2026-08-31"          # день до периода — не должен попадать


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_plan(status="Активный", docstatus=1):
    return _db_insert(
        "Installment Plan", imei="WK" + frappe.generate_hash(length=6),
        principal_amount=1000, financed_amount=1000, total_interest=0,
        total_amount=1000, paid_amount=0, remaining_balance=1000,
        start_date="2026-01-01", status=status, contract_status="Активный",
        docstatus=docstatus,
    )


def _seed_row(plan, due_date, amount, paid=0, status="Ожидает", payment=None, idx=1):
    return _db_insert(
        "Installment Schedule", parent=plan, parenttype="Installment Plan",
        parentfield="schedule", idx=idx, installment_number=idx,
        due_date=due_date, amount=amount, paid_amount=paid, status=status,
        payment_transaction=payment,
    )


def _seed_payment(plan, amount, date):
    return _db_insert(
        "Payment Transaction", reference_doctype="Installment Plan",
        reference_name=plan, amount=amount, payment_date=date,
        status="Завершен", docstatus=1,
    )


def _seed_allocation(plan, row, payment, amount, status="Активна"):
    """Запись журнала разноски — так это делает apply_payment в бою."""
    return _db_insert(
        "Payment Allocation", payment_transaction=payment.name,
        installment_plan=plan, schedule_row=row.name, allocated_amount=amount,
        allocation_date=payment.payment_date, status=status,
    )


class TestWeeklyCollectionReport(unittest.TestCase):
    """Проверки разностные.

    База теста общая с другими прогонами, поэтому абсолютные суммы в ней не
    гарантированы. Сравнивается вклад именно того, что сеет тест.
    """

    def setUp(self):
        frappe.db.savepoint("weekly_report")
        self.base = generate_collection_report(as_of=_END)

    def tearDown(self):
        frappe.db.rollback(save_point="weekly_report")

    def _delta(self, key):
        return generate_collection_report(as_of=_END)[key] - self.base[key]

    def test_period_is_seven_dates(self):
        r = generate_collection_report(as_of=_END)
        self.assertEqual(r["start_date"], _IN_FIRST)
        self.assertEqual(r["end_date"], _END)
        self.assertEqual(r["days"], 7)

    def test_obligation_before_the_period_is_excluded(self):
        plan = _seed_plan()
        _seed_row(plan.name, _OUT, 100)
        self.assertAlmostEqual(self._delta("expected"), 0, places=2)

    def test_expected_is_obligation_before_period_payments(self):
        plan = _seed_plan()
        pay = _seed_payment(plan.name, 40, _IN_FIRST)
        row = _seed_row(plan.name, _IN_FIRST, 100, paid=40, status="Частично",
                        payment=pay.name)
        _seed_allocation(plan.name, row, pay, 40)
        # Ожидание — все 100, а не 60: платёж периода не уменьшает то, что ждали.
        self.assertAlmostEqual(self._delta("expected"), 100, places=2)
        self.assertAlmostEqual(self._delta("collected_against_expected"), 40, places=2)

    def test_unrelated_payment_does_not_inflate_collection(self):
        """Наличная продажа и погашение старой просрочки — не сборы периода."""
        plan = _seed_plan()
        _seed_row(plan.name, _IN_FIRST, 100)
        _seed_payment(plan.name, 500, _IN_FIRST)          # ни к одной строке периода
        _db_insert("Payment Transaction", amount=900, payment_date=_IN_FIRST,
                   status="Завершен", docstatus=1)         # вообще без договора
        self.assertAlmostEqual(self._delta("collected_against_expected"), 0, places=2)

    def test_overdue_counts_only_the_remainder(self):
        plan = _seed_plan()
        _seed_row(plan.name, _OUT, 100, paid=40, status="Просрочен")
        self.assertAlmostEqual(self._delta("overdue"), 60, places=2)

    def test_draft_plan_is_excluded(self):
        plan = _seed_plan(docstatus=0)
        _seed_row(plan.name, _IN_FIRST, 100)
        self.assertAlmostEqual(self._delta("expected"), 0, places=2)

    def test_closed_plan_is_excluded(self):
        plan = _seed_plan(status="Списан")
        _seed_row(plan.name, _IN_FIRST, 100)
        self.assertAlmostEqual(self._delta("expected"), 0, places=2)

    def test_efficiency_is_collected_over_expected(self):
        r = generate_collection_report(as_of=_END)
        if r["expected"] > 0:
            self.assertAlmostEqual(
                r["efficiency"],
                round(r["collected_against_expected"] / r["expected"] * 100, 2),
                places=2,
            )
        else:
            self.assertEqual(r["efficiency"], 0)  # ноль в знаменателе не делит


class TestWeeklyCollectionIsExact(unittest.TestCase):
    """Числитель считается по журналу разноски, а не по ссылке строки.

    Пока считалось по Installment Schedule.payment_transaction, строку, закрытую
    двумя платежами, зачитывал только последний — и зачитывал ЦЕЛИКОМ. Платёж 40
    внутри периода приносил в числитель все 100, включая 60, полученные раньше.
    """

    def setUp(self):
        frappe.db.savepoint("weekly_exact")
        self.base = generate_collection_report(as_of=_END)

    def tearDown(self):
        frappe.db.rollback(save_point="weekly_exact")

    def _delta(self, key):
        return generate_collection_report(as_of=_END)[key] - self.base[key]

    def test_only_money_received_in_the_period_counts(self):
        plan = _seed_plan()
        row = _seed_row(plan.name, _IN_FIRST, 100, paid=100, status="Оплачен")

        early = _seed_payment(plan.name, 60, "2026-08-20")   # до периода
        inside = _seed_payment(plan.name, 40, _IN_FIRST)     # внутри периода
        for payment, amount in ((early, 60), (inside, 40)):
            _seed_allocation(plan.name, row, payment, amount)
        # Ссылка строки указывает на последний платёж — как это делает apply_payment.
        frappe.db.set_value("Installment Schedule", row.name, "payment_transaction",
                            inside.name, update_modified=False)

        self.assertAlmostEqual(self._delta("collected_against_expected"), 40, places=2)

    def test_reversed_allocation_does_not_count(self):
        plan = _seed_plan()
        row = _seed_row(plan.name, _IN_FIRST, 100, paid=0)
        cancelled = _seed_payment(plan.name, 40, _IN_FIRST)
        _seed_allocation(plan.name, row, cancelled, 40, status="Реверсирована")
        self.assertAlmostEqual(self._delta("collected_against_expected"), 0, places=2)
