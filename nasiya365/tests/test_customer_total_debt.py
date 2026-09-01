"""Регресс: total_debt клиента не должен множиться на число строк графика.

update_statistics считал SUM(remaining_balance) поверх LEFT JOIN со строками
графика, из-за чего план с 12 взносами давал долг в 13 раз больше реального
(12 взносов + строка аванса). Поле видно в карточке клиента, а через
available_limit оно ещё и блокирует создание новых договоров, когда включён
enforce_customer_credit_limit.

Ожидаемые суммы обновлены под кредитную базу «непогашенный основной долг без
будущих процентов» (решение владельца, 2026-09-01): долгом считается
финансируемая часть, а не остаток договора вместе с процентами. Инвариант теста
от этого не изменился — долг по-прежнему не зависит от числа строк графика.

Даты 2030 года — вне диапазона demo-данных dev-сайта.
"""

import unittest

import frappe


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


class TestCustomerTotalDebt(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("total_debt_test")
        self.customer = frappe.get_doc({
            "doctype": "Customer Profile",
            "first_name": "Тестовый", "last_name": "Долгов",
            "status": "Активный", "credit_limit": 5000,
            "message_language": "Русский",
        })
        self.customer.append("phone_numbers", {
            "phone_number": "+998900000001", "phone_type": "Мобильный", "is_primary": 1,
        })
        self.customer.insert(ignore_permissions=True)

    def tearDown(self):
        frappe.db.rollback(save_point="total_debt_test")

    def _plan_with_schedule(self, remaining, rows, interest=200):
        """financed + interest = total: иначе фикстура описывает невозможный договор."""
        financed = remaining - interest
        plan = _db_insert(
            "Installment Plan", customer=self.customer.name,
            principal_amount=financed, financed_amount=financed,
            total_interest=interest, total_amount=remaining, paid_amount=0,
            remaining_balance=remaining, start_date="2030-01-01",
            number_of_installments=rows, status="Активный",
            contract_type="Рассрочка (BNPL)", contract_date="2030-01-01", docstatus=1,
        )
        for i in range(rows):
            _db_insert(
                "Installment Schedule", parent=plan.name,
                parenttype="Installment Plan", parentfield="schedule",
                idx=i + 1, installment_number=i + 1, due_date="2030-02-01",
                amount=remaining / rows, status="Ожидает",
            )
        return plan

    def test_debt_equals_financed_principal_not_row_count_times_it(self):
        # Договор на 1200 к оплате, из них 200 — проценты: основной долг 1000.
        self._plan_with_schedule(remaining=1200, rows=12)
        self.customer.update_statistics()
        self.assertAlmostEqual(self.customer.total_debt, 1000.0, places=2)

    def test_debt_is_the_same_for_one_row_and_twelve(self):
        """Исходный инвариант теста — он и есть главный."""
        self._plan_with_schedule(remaining=1200, rows=12)
        self.customer.update_statistics()
        many = self.customer.total_debt
        frappe.db.delete("Installment Schedule", {"parent": ("!=", "")})
        self._plan_with_schedule(remaining=1200, rows=1)
        self.customer.update_statistics()
        self.assertAlmostEqual(self.customer.total_debt, many * 2, places=2)

    def test_available_limit_reflects_real_debt(self):
        self._plan_with_schedule(remaining=1200, rows=12)
        self.customer.update_statistics()
        self.assertAlmostEqual(self.customer.available_limit, 4000.0, places=2)

    def test_two_plans_each_counted_once(self):
        self._plan_with_schedule(remaining=1200, rows=12)
        self._plan_with_schedule(remaining=800, rows=6)
        self.customer.update_statistics()
        self.assertAlmostEqual(self.customer.total_debt, 1600.0, places=2)
        self.assertEqual(self.customer.active_contracts_count, 2)

    def test_plan_without_schedule_rows_still_counted(self):
        # LEFT JOIN даёт одну строку с NULL — план обязан посчитаться ровно раз.
        _db_insert(
            "Installment Plan", customer=self.customer.name,
            principal_amount=500, financed_amount=500, total_amount=500,
            remaining_balance=500, start_date="2030-01-01", number_of_installments=1,
            status="Активный", contract_type="Рассрочка (BNPL)",
            contract_date="2030-01-01", docstatus=1,
        )
        self.customer.update_statistics()
        self.assertAlmostEqual(self.customer.total_debt, 500.0, places=2)

    def test_closed_plans_do_not_count(self):
        plan = self._plan_with_schedule(remaining=1200, rows=12)
        frappe.db.set_value("Installment Plan", plan.name, "status", "Завершен")
        self.customer.update_statistics()
        self.assertAlmostEqual(self.customer.total_debt, 0.0, places=2)
