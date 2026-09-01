"""Журнал разноски платежей.

Installment Schedule хранит ОДНУ ссылку на платёж, и apply_payment её
перезаписывал. Комментарий в коде отмены прямо формулировал неверную посылку:
«the transaction is the sole contributor — Frappe stores at most one
payment_transaction reference per row».

Строку 100 закрыли платежами 60 и 40. Отмена второго ставила paid_amount = 0:
клиент терял внесённые 60, а план — 100 вместо 40.
"""

import unittest

import frappe
from frappe.utils import flt

from nasiya365.nasiya365.doctype.payment_allocation.payment_allocation import (
    STATUS_ACTIVE,
    STATUS_REVERSED,
)
from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
    _deallocate_payment_from_installment_plan,
)

_START = "2026-01-01"


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_customer():
    return _db_insert("Customer Profile", customer_name="Разноска Тест",
                      status="Активный", credit_limit=0)


def _seed_plan(rows):
    """План с готовым графиком: rows — список сумм строк.

    Поля заполняются полностью: план сохраняется через plan.save(), а он
    проверяет обязательные — договор без клиента и даты не существует.
    """
    total = sum(rows)
    plan = _db_insert(
        "Installment Plan", imei="AL" + frappe.generate_hash(length=6),
        customer=_seed_customer().name, contract_type="Рассрочка (BNPL)",
        contract_date=_START,
        principal_amount=total, financed_amount=total, total_interest=0,
        total_amount=total, paid_amount=0, remaining_balance=total,
        number_of_installments=len(rows), start_date=_START,
        status="Активный", contract_status="Подписан", docstatus=1,
        formula_version=2,
    )
    for i, amount in enumerate(rows):
        _db_insert(
            "Installment Schedule", parent=plan.name, parenttype="Installment Plan",
            parentfield="schedule", idx=i + 1, installment_number=i + 1,
            due_date="2026-02-01", amount=amount, paid_amount=0, status="Ожидает",
        )
    return plan


def _seed_payment(plan, amount):
    return _db_insert(
        "Payment Transaction", reference_doctype="Installment Plan",
        reference_name=plan, amount=amount, payment_date=_START,
        status="Завершен", docstatus=1,
    )


def _pay(plan_name, payment_name, amount):
    plan = frappe.get_doc("Installment Plan", plan_name)
    plan.apply_payment(amount, payment_transaction=payment_name, payment_date=_START)
    plan.flags.ignore_validate_update_after_submit = True
    frappe.flags.nasiya_plan_allocating_payment = True
    try:
        plan.save(ignore_permissions=True)
    finally:
        frappe.flags.nasiya_plan_allocating_payment = False


def _row_paid(plan_name):
    return [
        flt(r.paid_amount)
        for r in frappe.get_all("Installment Schedule", filters={"parent": plan_name},
                                fields=["paid_amount", "idx"], order_by="idx asc")
    ]


def _allocations(payment_name):
    return frappe.get_all("Payment Allocation", filters={"payment_transaction": payment_name},
                          fields=["schedule_row", "allocated_amount", "status"])


class TestPaymentAllocationLedger(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("pay_alloc")

    def tearDown(self):
        frappe.db.rollback(save_point="pay_alloc")

    def test_allocation_is_recorded_for_each_touched_row(self):
        plan = _seed_plan([100, 100])
        pt = _seed_payment(plan.name, 150)
        _pay(plan.name, pt.name, 150)

        allocs = _allocations(pt.name)
        self.assertEqual(len(allocs), 2)
        self.assertAlmostEqual(sum(flt(a.allocated_amount) for a in allocs), 150, places=2)
        self.assertTrue(all(a.status == STATUS_ACTIVE for a in allocs))

    def test_allocations_sum_to_the_payment(self):
        plan = _seed_plan([100, 100, 100])
        pt = _seed_payment(plan.name, 250)
        _pay(plan.name, pt.name, 250)
        self.assertAlmostEqual(
            sum(flt(a.allocated_amount) for a in _allocations(pt.name)), 250, places=2)

    def test_cancelling_the_second_payment_keeps_the_first(self):
        """Главный дефект: отмена второго платежа стирала деньги первого."""
        plan = _seed_plan([100])
        first = _seed_payment(plan.name, 60)
        second = _seed_payment(plan.name, 40)
        _pay(plan.name, first.name, 60)
        _pay(plan.name, second.name, 40)
        self.assertEqual(_row_paid(plan.name), [100.0])

        _deallocate_payment_from_installment_plan(frappe.get_doc("Payment Transaction", second.name))

        self.assertEqual(_row_paid(plan.name), [60.0])
        self.assertAlmostEqual(
            flt(frappe.db.get_value("Installment Plan", plan.name, "paid_amount")), 60, places=2)

    def test_cancelling_the_first_payment_keeps_the_second(self):
        plan = _seed_plan([100])
        first = _seed_payment(plan.name, 60)
        second = _seed_payment(plan.name, 40)
        _pay(plan.name, first.name, 60)
        _pay(plan.name, second.name, 40)

        _deallocate_payment_from_installment_plan(frappe.get_doc("Payment Transaction", first.name))

        self.assertEqual(_row_paid(plan.name), [40.0])

    def test_reversal_marks_history_instead_of_deleting_it(self):
        plan = _seed_plan([100])
        pt = _seed_payment(plan.name, 60)
        _pay(plan.name, pt.name, 60)

        _deallocate_payment_from_installment_plan(frappe.get_doc("Payment Transaction", pt.name))

        allocs = _allocations(pt.name)
        self.assertEqual(len(allocs), 1, "запись исчезла — история потеряна")
        self.assertEqual(allocs[0].status, STATUS_REVERSED)

    def test_reversing_twice_changes_nothing(self):
        plan = _seed_plan([100])
        pt = _seed_payment(plan.name, 60)
        _pay(plan.name, pt.name, 60)
        doc = frappe.get_doc("Payment Transaction", pt.name)

        _deallocate_payment_from_installment_plan(doc)
        after_first = _row_paid(plan.name)
        _deallocate_payment_from_installment_plan(doc)

        self.assertEqual(_row_paid(plan.name), after_first)

    def test_allocation_spanning_two_rows_reverses_both(self):
        plan = _seed_plan([100, 100])
        first = _seed_payment(plan.name, 100)
        second = _seed_payment(plan.name, 120)
        _pay(plan.name, first.name, 100)
        _pay(plan.name, second.name, 120)
        self.assertEqual(_row_paid(plan.name), [100.0, 100.0])

        _deallocate_payment_from_installment_plan(frappe.get_doc("Payment Transaction", second.name))

        # Второй платёж дал 0 в первую строку (уже закрыта) и 100 во вторую;
        # переплата 20 в график не пошла.
        self.assertEqual(_row_paid(plan.name), [100.0, 0.0])


class TestBackfillReplay(unittest.TestCase):
    """Восстановление журнала для платежей, проведённых до его появления.

    Сбросом строк историю не восстановить: у строки одна ссылка на платёж.
    Платежи проигрываются заново тем же кодом, что разносит их в бою, и итог
    сверяется с фактом — только совпавшие планы попадают в журнал.
    """

    def setUp(self):
        frappe.db.savepoint("backfill")

    def tearDown(self):
        frappe.db.rollback(save_point="backfill")

    def _plan_with_history(self, rows, payments):
        """План с платежами, но БЕЗ записей журнала — как до миграции."""
        plan = _seed_plan(rows)
        for amount in payments:
            pt = _seed_payment(plan.name, amount)
            _pay(plan.name, pt.name, amount)
        frappe.db.delete("Payment Allocation", {"installment_plan": plan.name})
        return plan

    def test_replay_reproduces_the_actual_state(self):
        from nasiya365.backfill_payment_allocations import _replay

        plan = self._plan_with_history([100, 100], [60, 90])
        allocations, mismatches = _replay(plan.name)
        self.assertEqual(mismatches, [], "проигрывание разошлось с фактом")
        self.assertAlmostEqual(sum(a[2] for a in allocations), 150, places=2)

    def test_replay_reports_a_hand_edited_plan(self):
        """Строку правили руками — FIFO такого не воспроизведёт, и это видно."""
        from nasiya365.backfill_payment_allocations import _replay

        plan = self._plan_with_history([100, 100], [60])
        row = frappe.get_all("Installment Schedule", filters={"parent": plan.name},
                             order_by="idx desc", limit=1)[0]
        frappe.db.set_value("Installment Schedule", row.name, "paid_amount", 55,
                            update_modified=False)

        _, mismatches = _replay(plan.name)
        self.assertTrue(mismatches, "расхождение не замечено")

    def test_replay_handles_a_completed_plan(self):
        """План сегодня завершён, но платежи вносились, когда он был активен."""
        from nasiya365.backfill_payment_allocations import _replay

        plan = self._plan_with_history([100], [100])
        self.assertEqual(
            frappe.db.get_value("Installment Plan", plan.name, "status"), "Завершен")
        _, mismatches = _replay(plan.name)
        self.assertEqual(mismatches, [])

    def test_recording_is_idempotent(self):
        from nasiya365.nasiya365.doctype.payment_allocation import payment_allocation as pa

        plan = _seed_plan([100])
        pt = _seed_payment(plan.name, 60)
        _pay(plan.name, pt.name, 60)
        before = len(_allocations(pt.name))
        pa.record(pt.name, plan.name, [("любая-строка", 60)])
        self.assertEqual(len(_allocations(pt.name)), before, "журнал удвоился")
