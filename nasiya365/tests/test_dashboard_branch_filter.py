"""Фильтр филиала в KPI дашборда.

Параметр `branch` приходил в _kpi_metrics и возвращался эхом в `filters`, но ни
в один SQL-запрос не попадал: интерфейс показывал, что фильтр применён, а цифры
были общие по всем филиалам.

Права и выбор — разные вещи и должны ПЕРЕСЕКАТЬСЯ: выбор филиала не может
расширить доступ, а права не должны отменять выбор.
"""

import unittest

import frappe

from nasiya365.api.bnpl_dashboard import _kpi_metrics

_DATE = "2026-09-01"
_PAST = "2026-08-01"


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_branch(city):
    return _db_insert("Branch", branch_name="Филиал " + city, city=city)


def _seed_plan(branch, remaining, overdue_amount, collected):
    plan = _db_insert(
        "Installment Plan", imei="BR" + frappe.generate_hash(length=6),
        branch=branch, principal_amount=1000, financed_amount=1000,
        total_interest=0, total_amount=1000, paid_amount=0,
        remaining_balance=remaining, start_date=_PAST,
        status="Активный", contract_status="Активный", docstatus=1,
    )
    _db_insert(
        "Installment Schedule", parent=plan.name, parenttype="Installment Plan",
        parentfield="schedule", idx=1, installment_number=1,
        due_date=_PAST, amount=overdue_amount, paid_amount=0, status="Просрочен",
    )
    _db_insert(
        "Payment Transaction", reference_doctype="Installment Plan",
        reference_name=plan.name, amount=collected, payment_date=_DATE,
        status="Завершен", docstatus=1,
    )
    return plan


class TestDashboardBranchFilter(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("kpi_branch")
        self.a = _seed_branch("Andijon" + frappe.generate_hash(length=4))
        self.b = _seed_branch("Buxoro" + frappe.generate_hash(length=4))
        _seed_plan(self.a.name, remaining=500, overdue_amount=100, collected=70)
        _seed_plan(self.b.name, remaining=300, overdue_amount=40, collected=30)

    def tearDown(self):
        frappe.db.rollback(save_point="kpi_branch")

    def test_selected_branch_narrows_every_metric(self):
        both = _kpi_metrics(_DATE)
        only_a = _kpi_metrics(_DATE, branch=self.a.name)

        self.assertAlmostEqual(only_a["outstanding_amount"], 500, places=2)
        self.assertAlmostEqual(only_a["overdue_amount"], 100, places=2)
        self.assertAlmostEqual(only_a["cash_collected_today"], 70, places=2)
        self.assertEqual(only_a["active_contracts"], 1)

        self.assertGreater(both["outstanding_amount"], only_a["outstanding_amount"])
        self.assertGreater(both["overdue_amount"], only_a["overdue_amount"])

    def test_other_branch_gets_its_own_numbers(self):
        only_b = _kpi_metrics(_DATE, branch=self.b.name)
        self.assertAlmostEqual(only_b["outstanding_amount"], 300, places=2)
        self.assertAlmostEqual(only_b["overdue_amount"], 40, places=2)
        self.assertEqual(only_b["active_contracts"], 1)

    def test_unknown_branch_gives_nothing(self):
        none = _kpi_metrics(_DATE, branch="НЕТ-ТАКОГО-ФИЛИАЛА")
        self.assertAlmostEqual(none["outstanding_amount"], 0, places=2)
        self.assertEqual(none["active_contracts"], 0)

    def test_revenue_mtd_follows_the_filter(self):
        # Поступления МТД считаются отдельной функцией — она тоже должна знать фильтр.
        only_a = _kpi_metrics(_DATE, branch=self.a.name)
        both = _kpi_metrics(_DATE)
        self.assertLessEqual(only_a["revenue_mtd"], both["revenue_mtd"])
        self.assertNotEqual(only_a["revenue_mtd"], both["revenue_mtd"])
