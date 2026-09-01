"""Модель рассрочки: flat monthly со сроком по календарю.

Решение владельца от 2026-09-01. До него срок брался как ЧИСЛО ПЛАТЕЖЕЙ, а
ставка месячная: недельный график из 12 платежей длиной 2,76 месяца получал
процент за 12 месяцев — переплата в 4,35 раза.

Отдельно: калькулятор делил ставку на 12 как годовую и применял аннуитет, а
договор считал flat по месячной. На вводе «2» калькулятор показывал 10.87
процентов там, где договор печатал 240.00 — разница в 22 раза.
"""

import unittest

import frappe
from frappe.utils import flt

from nasiya365.finance import (
    FORMULA_VERSION_CALENDAR,
    FORMULA_VERSION_LEGACY,
    flat_interest,
    schedule_term_months,
)

_MONTHLY = "Ежемесячно (Monthly)"
_WEEKLY = "Еженедельно (Weekly)"
_BIWEEKLY = "Раз в две недели (Biweekly)"


class TestScheduleTerm(unittest.TestCase):
    def test_monthly_term_is_the_payment_count(self):
        self.assertAlmostEqual(schedule_term_months(12, _MONTHLY), 12.0, places=4)

    def test_weekly_term_is_shorter_than_the_payment_count(self):
        # 12 недель — это 2,76 месяца, а не 12.
        self.assertAlmostEqual(schedule_term_months(12, _WEEKLY), 12 * 7 / 30.44, places=4)
        self.assertLess(schedule_term_months(12, _WEEKLY), 3)

    def test_biweekly_term(self):
        self.assertAlmostEqual(schedule_term_months(12, _BIWEEKLY), 12 * 14 / 30.44, places=4)

    def test_no_payments_no_term(self):
        self.assertAlmostEqual(schedule_term_months(0, _MONTHLY), 0.0, places=4)


class TestFlatInterest(unittest.TestCase):
    def test_monthly_plan_is_unchanged(self):
        """Месячный график считался верно и меняться не должен."""
        self.assertAlmostEqual(
            flat_interest(1000, 2, schedule_term_months(12, _MONTHLY)), 240.00, places=2)

    def test_weekly_plan_no_longer_overcharges(self):
        self.assertAlmostEqual(
            flat_interest(1000, 2, schedule_term_months(12, _WEEKLY)), 55.19, places=2)

    def test_weekly_overcharge_was_four_times(self):
        correct = flat_interest(1000, 2, schedule_term_months(12, _WEEKLY))
        legacy = flat_interest(1000, 2, 12)          # как считалось раньше
        self.assertAlmostEqual(legacy / correct, 30.44 / 7, places=2)

    def test_zero_rate_costs_nothing(self):
        self.assertAlmostEqual(flat_interest(1000, 0, 12), 0.0, places=2)


def _plan(principal, down, rate, num, frequency, version=None, existing=False):
    """Договор в том же состоянии, в каком его видит validate().

    insert() выставляет __islocal ДО валидации (document.py:470), поэтому у
    создаваемого договора is_new() истинно. Документ, собранный в Python и не
    прошедший insert(), этого флага не имеет — и без него тест проверял бы не
    тот путь.
    """
    doc = frappe.get_doc({
        "doctype": "Installment Plan", "principal_amount": principal,
        "down_payment": down, "interest_rate": rate,
        "number_of_installments": num, "frequency": frequency,
        "start_date": "2026-01-01",
    })
    if not existing:
        doc.set("__islocal", True)
    if version:
        doc.formula_version = version
    return doc


class TestPlanTotals(unittest.TestCase):
    def test_weekly_plan_charges_for_its_real_length(self):
        p = _plan(1000, 0, 2, 12, _WEEKLY)
        p.calculate_amounts()
        self.assertAlmostEqual(p.total_interest, 55.19, places=2)

    def test_monthly_plan_keeps_its_numbers(self):
        p = _plan(1000, 0, 2, 12, _MONTHLY)
        p.calculate_amounts()
        self.assertAlmostEqual(p.total_interest, 240.00, places=2)

    def test_new_plan_uses_the_calendar_formula(self):
        p = _plan(1000, 0, 2, 12, _WEEKLY)
        p.calculate_amounts()
        self.assertEqual(int(p.formula_version), FORMULA_VERSION_CALENDAR)

    def test_existing_plan_keeps_its_formula(self):
        """Исторические договоры не пересчитываются: у них своя версия."""
        p = _plan(1000, 0, 2, 12, _WEEKLY, version=FORMULA_VERSION_LEGACY, existing=True)
        p.calculate_amounts()
        self.assertAlmostEqual(p.total_interest, 240.00, places=2)
        self.assertEqual(int(p.formula_version), FORMULA_VERSION_LEGACY)

    def test_totals_add_up(self):
        p = _plan(1000, 200, 2, 6, _MONTHLY)
        p.calculate_amounts()
        self.assertAlmostEqual(p.financed_amount, 800, places=2)
        self.assertAlmostEqual(p.total_interest, 800 * 0.02 * 6, places=2)


class TestCalculatorAgreesWithContract(unittest.TestCase):
    """Калькулятор и договор обязаны давать одно число на одном вводе."""

    def test_preview_matches_the_plan(self):
        from nasiya365.nasiya365.doctype.installment_plan.installment_plan import (
            _build_installment_preview,
        )

        for freq in (_MONTHLY, _WEEKLY, _BIWEEKLY):
            preview = _build_installment_preview(1200, 200, 2, 6, freq, "2026-01-01")
            plan = _plan(1200, 200, 2, 6, freq)
            # Тот же порядок, что в validate(): итоги, график, снова итоги —
            # total_amount включает аванс только когда строка 0 уже существует.
            plan.calculate_amounts()
            plan.generate_schedule()
            plan.calculate_amounts()
            self.assertAlmostEqual(
                preview["total_interest"], plan.total_interest, places=2,
                msg=f"{freq}: калькулятор и договор разошлись")
            self.assertAlmostEqual(
                preview["total_amount"], plan.total_amount, places=2, msg=freq)


class TestLegacyPlansAreLeftAlone(unittest.TestCase):
    def test_saved_plan_without_version_keeps_the_old_formula(self):
        """Договор, созданный до правки и не тронутый патчем, не пересчитывается."""
        p = _plan(1000, 0, 2, 12, _WEEKLY, existing=True)
        p.calculate_amounts()
        self.assertAlmostEqual(p.total_interest, 240.00, places=2)
        self.assertEqual(int(p.formula_version), FORMULA_VERSION_LEGACY)


class TestPlanInputValidation(unittest.TestCase):
    """Серверные ограничения на входные значения.

    До этого проверялось только «оплачено не отрицательно»: договор можно было
    сохранить с нулевой ценой, авансом больше цены или отрицательной ставкой.
    """

    def _check(self, **overrides):
        args = dict(principal=1000, down=0, rate=2, num=6, frequency=_MONTHLY)
        args.update(overrides)
        p = _plan(args["principal"], args["down"], args["rate"], args["num"],
                  args["frequency"])
        p.validate_financial_inputs()

    def test_valid_plan_passes(self):
        self._check()

    def test_zero_principal_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._check(principal=0)

    def test_negative_principal_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._check(principal=-100)

    def test_down_payment_above_principal_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._check(principal=1000, down=1200)

    def test_down_payment_equal_to_principal_is_allowed(self):
        # Полная предоплата — законная сделка, финансируется ноль.
        self._check(principal=1000, down=1000)

    def test_negative_down_payment_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._check(down=-1)

    def test_negative_rate_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._check(rate=-1)

    def test_zero_installments_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._check(num=0)

    def test_negative_installments_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._check(num=-3)


class TestScheduleSumIsEnforced(unittest.TestCase):
    def test_broken_schedule_sum_blocks_the_save(self):
        """Раньше расхождение только предупреждало — и договор сохранялся."""
        p = _plan(1000, 0, 0, 2, _MONTHLY)
        p.calculate_amounts()
        p.generate_schedule()
        p.calculate_amounts()
        p.schedule[0].amount = flt(p.schedule[0].amount) + 100  # ломаем график
        with self.assertRaises(frappe.ValidationError):
            p._validate_schedule_sum()

    def test_intact_schedule_passes(self):
        p = _plan(1000, 0, 2, 6, _MONTHLY)
        p.calculate_amounts()
        p.generate_schedule()
        p.calculate_amounts()
        p._validate_schedule_sum()
