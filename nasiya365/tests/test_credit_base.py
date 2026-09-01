"""Кредитная база: непогашенный основной долг без будущих процентов.

Решение владельца от 2026-09-01. До него долгом считался весь остаток договора
вместе с процентами: клиент со ставкой 2% в месяц на год «съедал» лимит на 24%
больше, чем стоил купленный товар.

Одна и та же база применяется к total_debt, available_limit, проверке нового
договора и черновикам — иначе проверка строже учёта, и это расходится молча.
"""

import unittest

import frappe

from nasiya365.finance import outstanding_principal


class TestOutstandingPrincipal(unittest.TestCase):
    """Формула отдельно от базы — она чистая и проверяется числами."""

    def test_nothing_paid_leaves_the_whole_financed_principal(self):
        # Цена 1000, аванс 200, проценты 200. Долг — 800, а не 1200.
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=800, total_interest=200,
                                  paid_amount=0, down_payment=200,
                                  has_down_payment_row=True), 800, places=2)

    def test_down_payment_alone_does_not_reduce_principal(self):
        """Аванс — не погашение займа, он и так не входил в финансируемую часть."""
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=800, total_interest=200,
                                  paid_amount=200, down_payment=200,
                                  has_down_payment_row=True), 800, places=2)

    def test_instalments_reduce_principal_proportionally(self):
        # Внесено 200 аванса и 250 взносов. 250 из 1000 — четверть: 800 × 0.75.
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=800, total_interest=200,
                                  paid_amount=450, down_payment=200,
                                  has_down_payment_row=True), 600, places=2)

    def test_fully_paid_plan_owes_nothing(self):
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=800, total_interest=200,
                                  paid_amount=1200, down_payment=200,
                                  has_down_payment_row=True), 0, places=2)

    def test_overpayment_does_not_go_negative(self):
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=800, total_interest=200,
                                  paid_amount=5000, down_payment=200,
                                  has_down_payment_row=True), 0, places=2)

    def test_old_style_plan_without_row_zero(self):
        """У старых договоров аванса нет в графике и нет в paid_amount."""
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=800, total_interest=200,
                                  paid_amount=250, down_payment=200,
                                  has_down_payment_row=False), 600, places=2)

    def test_interest_free_plan_is_plain_arithmetic(self):
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=1000, total_interest=0,
                                  paid_amount=400, down_payment=0,
                                  has_down_payment_row=False), 600, places=2)

    def test_empty_plan_does_not_divide_by_zero(self):
        self.assertAlmostEqual(
            outstanding_principal(financed_amount=0, total_interest=0,
                                  paid_amount=0, down_payment=0,
                                  has_down_payment_row=False), 0, places=2)

    def test_future_interest_is_never_part_of_the_debt(self):
        """Суть решения: долг не растёт от того, что ставка выше."""
        cheap = outstanding_principal(financed_amount=1000, total_interest=0,
                                      paid_amount=0, down_payment=0,
                                      has_down_payment_row=False)
        pricey = outstanding_principal(financed_amount=1000, total_interest=240,
                                       paid_amount=0, down_payment=0,
                                       has_down_payment_row=False)
        self.assertAlmostEqual(cheap, pricey, places=2)


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_customer(credit_limit=0):
    return _db_insert("Customer Profile", customer_name="Кредит Тест",
                      status="Активный", credit_limit=credit_limit)


def _seed_plan(customer, financed, interest, paid, down=0, with_row_zero=False,
               rows=1, status="Активный"):
    total = financed + interest + (down if with_row_zero else 0)
    plan = _db_insert(
        "Installment Plan", customer=customer, imei="CB" + frappe.generate_hash(length=6),
        principal_amount=financed + down, down_payment=down, financed_amount=financed,
        total_interest=interest, total_amount=total, paid_amount=paid,
        remaining_balance=total - paid, start_date="2026-01-01",
        status=status, contract_status="Активный", docstatus=1,
    )
    idx = 1
    if with_row_zero:
        _db_insert("Installment Schedule", parent=plan.name, parenttype="Installment Plan",
                   parentfield="schedule", idx=idx, installment_number=0,
                   due_date="2026-01-01", amount=down, paid_amount=min(paid, down),
                   status="Оплачен" if paid >= down else "Ожидает")
        idx += 1
    for i in range(rows):
        _db_insert("Installment Schedule", parent=plan.name, parenttype="Installment Plan",
                   parentfield="schedule", idx=idx, installment_number=i + 1,
                   due_date="2026-02-01", amount=(financed + interest) / rows,
                   paid_amount=0, status="Ожидает")
        idx += 1
    return plan


class TestCreditBaseOnCustomer(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("credit_base")

    def tearDown(self):
        frappe.db.rollback(save_point="credit_base")

    def _debt(self, customer):
        doc = frappe.get_doc("Customer Profile", customer)
        doc.update_statistics()
        return frappe.db.get_value("Customer Profile", customer, "total_debt")

    def test_debt_excludes_future_interest(self):
        c = _seed_customer()
        _seed_plan(c.name, financed=800, interest=200, paid=450, down=200, with_row_zero=True)
        # Остаток договора был бы 1200 − 450 = 750. Основной долг — 600.
        self.assertAlmostEqual(self._debt(c.name), 600, places=2)

    def test_two_plans_add_up(self):
        c = _seed_customer()
        _seed_plan(c.name, financed=800, interest=200, paid=0)
        _seed_plan(c.name, financed=500, interest=100, paid=0)
        self.assertAlmostEqual(self._debt(c.name), 1300, places=2)

    def test_debt_does_not_depend_on_schedule_row_count(self):
        """Тот же дефект, что чинил коммит 7faf127, — теперь и на новой базе."""
        c1, c12 = _seed_customer(), _seed_customer()
        _seed_plan(c1.name, financed=800, interest=200, paid=0, rows=1)
        _seed_plan(c12.name, financed=800, interest=200, paid=0, rows=12)
        self.assertAlmostEqual(self._debt(c1.name), self._debt(c12.name), places=2)

    def test_closed_plans_are_excluded(self):
        c = _seed_customer()
        _seed_plan(c.name, financed=800, interest=200, paid=0, status="Списан")
        self.assertAlmostEqual(self._debt(c.name), 0, places=2)

    def test_available_limit_uses_the_same_base(self):
        c = _seed_customer(credit_limit=2000)
        _seed_plan(c.name, financed=800, interest=200, paid=0)
        doc = frappe.get_doc("Customer Profile", c.name)
        doc.update_statistics()
        # 2000 − 800, а не 2000 − 1000.
        self.assertAlmostEqual(
            frappe.db.get_value("Customer Profile", c.name, "available_limit"), 1200, places=2)


class TestCreditBaseOnNewPlan(unittest.TestCase):
    """Проверка нового договора должна мерить ту же величину, что и учёт.

    Раньше сверялась полная цена товара, а долгом становился остаток с
    процентами: проверка и учёт говорили о разном, и расхождение было молчаливым.
    """

    def setUp(self):
        frappe.db.savepoint("credit_base_plan")
        self._old = frappe.db.get_single_value("Merchant Settings",
                                               "enforce_customer_credit_limit")
        frappe.db.set_single_value("Merchant Settings", "enforce_customer_credit_limit", 1)

    def tearDown(self):
        frappe.db.set_single_value("Merchant Settings", "enforce_customer_credit_limit",
                                   self._old)
        frappe.db.rollback(save_point="credit_base_plan")

    def _new_plan(self, customer, principal, down):
        return frappe.get_doc({
            "doctype": "Installment Plan", "customer": customer,
            "principal_amount": principal, "down_payment": down,
            "financed_amount": principal - down, "total_interest": 0,
            "number_of_installments": 6, "interest_rate": 0,
            "start_date": "2026-01-01", "frequency": "Ежемесячно (Monthly)",
        })

    def test_down_payment_does_not_consume_the_limit(self):
        """Аванс платится сразу и займом не становится."""
        c = _seed_customer(credit_limit=1000)
        frappe.get_doc("Customer Profile", c.name).update_statistics()
        # Товар за 1200 с авансом 400: в долг уходит 800, лимита хватает.
        self._new_plan(c.name, principal=1200, down=400).validate_customer_limit()

    def test_financed_part_above_the_limit_is_rejected(self):
        c = _seed_customer(credit_limit=1000)
        frappe.get_doc("Customer Profile", c.name).update_statistics()
        with self.assertRaises(frappe.ValidationError):
            self._new_plan(c.name, principal=1200, down=100).validate_customer_limit()

    def test_existing_debt_is_counted_by_the_same_base(self):
        c = _seed_customer(credit_limit=1000)
        # Договор на 800 основного долга плюс 200 процентов: занято 800, свободно 200.
        _seed_plan(c.name, financed=800, interest=200, paid=0)
        frappe.get_doc("Customer Profile", c.name).update_statistics()
        self._new_plan(c.name, principal=200, down=0).validate_customer_limit()
        with self.assertRaises(frappe.ValidationError):
            self._new_plan(c.name, principal=250, down=0).validate_customer_limit()
