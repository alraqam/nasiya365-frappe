"""Отчёт «Сборы и просрочка» против ночной разметки просрочки.

Ночная задача check_overdue_installments переводит строки
('Ожидает','Частично','Pending') → 'Просрочен'. Дашборд это учитывает явно,
отчёт — нет: строка исчезала из отчёта ровно в тот момент, когда становилась
просроченной. Замер на стенде на одних данных: дашборд 1 924,65, отчёт 0,00.
"""

import unittest
from unittest import mock

import frappe

from nasiya365.nasiya365.report.collections_and_overdue.collections_and_overdue import execute
from nasiya365.tasks.daily import check_overdue_installments

_TODAY = "2026-09-01"
_PAST = "2026-08-01"


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_plan_with_overdue_row(status="Ожидает", amount=100, paid=0):
    plan = _db_insert(
        "Installment Plan", imei="COLL" + frappe.generate_hash(length=5),
        customer_name="Тест Клиент", principal_amount=1000, financed_amount=1000,
        total_interest=0, total_amount=1000, paid_amount=paid,
        remaining_balance=1000 - paid, start_date=_PAST,
        status="Активный", contract_status="Активный", docstatus=1,
    )
    _db_insert(
        "Installment Schedule", parent=plan.name, parenttype="Installment Plan",
        parentfield="schedule", idx=1, installment_number=1,
        due_date=_PAST, amount=amount, paid_amount=paid, status=status,
    )
    return plan


def _overdue_for(plan_name):
    _, data = execute({"from_date": _PAST, "to_date": _TODAY})[:2]
    for row in data:
        if row["plan"] == plan_name:
            return row["overdue_amount"]
    return None


class TestCollectionsOverdueReport(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("coll_report")

    def tearDown(self):
        frappe.db.rollback(save_point="coll_report")

    def test_pending_overdue_row_is_reported(self):
        plan = _seed_plan_with_overdue_row(status="Ожидает")
        self.assertAlmostEqual(_overdue_for(plan.name), 100, places=2)

    def test_row_marked_overdue_by_the_nightly_job_is_still_reported(self):
        """Главный регресс: после ночной разметки строка не должна исчезать."""
        plan = _seed_plan_with_overdue_row(status="Ожидает")
        # Задача коммитит — без заглушки savepoint теста погибнет вместе с изоляцией.
        with mock.patch.object(frappe.db, "commit"):
            check_overdue_installments()
        self.assertEqual(
            frappe.db.get_value("Installment Schedule", {"parent": plan.name}, "status"),
            "Просрочен",
            "ночная задача не разметила строку — тест проверяет не то",
        )
        self.assertAlmostEqual(_overdue_for(plan.name), 100, places=2)

    def test_partially_paid_overdue_row_counts_only_the_remainder(self):
        plan = _seed_plan_with_overdue_row(status="Просрочен", amount=100, paid=40)
        self.assertAlmostEqual(_overdue_for(plan.name), 60, places=2)

    def test_settled_row_is_not_overdue(self):
        plan = _seed_plan_with_overdue_row(status="Просрочен", amount=100, paid=100)
        self.assertAlmostEqual(_overdue_for(plan.name), 0, places=2)
