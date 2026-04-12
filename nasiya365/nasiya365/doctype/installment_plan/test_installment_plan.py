"""
Integration tests for InstallmentPlan core business logic.

Run with:
  bench --site my.nasiya365.uz run-tests --app nasiya365 \
        --module nasiya365.nasiya365.doctype.installment_plan.test_installment_plan
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, flt, today


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_branch(branch_name="Test Branch", city="Test City"):
    existing = frappe.db.get_value("Branch", {"branch_name": branch_name}, "name")
    if existing:
        return existing
    doc = frappe.get_doc(
        {"doctype": "Branch", "branch_name": branch_name, "city": city}
    ).insert(ignore_permissions=True)
    return doc.name


def _make_customer(name_prefix="Test Customer", credit_limit=5000.0):
    import random
    suffix = str(random.randint(10000, 99999))
    c = frappe.get_doc(
        {
            "doctype": "Customer Profile",
            "first_name": name_prefix,
            "last_name": suffix,
            "status": "Активный",
            "message_language": "Русский",
            "credit_limit": credit_limit,
            "available_limit": credit_limit,
            "phone_numbers": [
                {
                    "phone_number": f"+99890{suffix}",
                    "phone_type": "Мобильный",
                    "is_primary": 1,
                }
            ],
        }
    )
    c.insert(ignore_permissions=True)
    return c.name


def _make_plan(customer, principal=1000.0, months=6, interest_rate=5.0, down_payment=0.0):
    """Create and insert a draft Installment Plan."""
    plan = frappe.get_doc(
        {
            "doctype": "Installment Plan",
            "customer": customer,
            "principal_amount": principal,
            "down_payment": down_payment,
            "number_of_installments": months,
            "frequency": "Ежемесячно",
            "interest_rate": interest_rate,
            "start_date": today(),
        }
    )
    plan.insert(ignore_permissions=True)
    return plan


def _make_payment(customer, plan_name, amount):
    """Insert a Payment Transaction and return the reloaded plan."""
    pt = frappe.get_doc(
        {
            "doctype": "Payment Transaction",
            "customer": customer,
            "reference_doctype": "Installment Plan",
            "reference_name": plan_name,
            "payment_date": today(),
            "send_payment_sms": 0,
            "payment_lines": [
                {"payment_method": "Наличные USD", "currency": "USD", "amount": amount}
            ],
        }
    )
    pt.insert(ignore_permissions=True)
    return pt


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestInstallmentPlanSchedule(FrappeTestCase):
    """Schedule generation and financial totals."""

    def setUp(self):
        _make_branch()

    def tearDown(self):
        frappe.db.rollback()

    def test_schedule_row_count(self):
        plan = _make_plan(_make_customer(), principal=1200, months=6)
        self.assertEqual(len(plan.schedule), 6)

    def test_schedule_total_matches_total_amount(self):
        plan = _make_plan(_make_customer(), principal=1200, months=6, interest_rate=5)
        schedule_sum = round(sum(flt(r.amount) for r in plan.schedule), 2)
        self.assertAlmostEqual(schedule_sum, flt(plan.total_amount), places=2)

    def test_last_row_absorbs_rounding(self):
        """No residual: schedule sum must equal total_amount exactly (within 1 cent)."""
        plan = _make_plan(_make_customer(), principal=1000, months=3, interest_rate=7)
        schedule_sum = sum(flt(r.amount) for r in plan.schedule)
        diff = abs(schedule_sum - flt(plan.total_amount))
        self.assertLess(diff, 0.02)

    def test_zero_interest_schedule(self):
        plan = _make_plan(_make_customer(), principal=600, months=6, interest_rate=0)
        for row in plan.schedule:
            self.assertAlmostEqual(flt(row.amount), 100.0, places=2)

    def test_down_payment_reduces_financed_amount(self):
        plan = _make_plan(
            _make_customer(), principal=1000, months=6,
            interest_rate=0, down_payment=200
        )
        self.assertAlmostEqual(flt(plan.financed_amount), 800.0, places=2)


class TestCreditLimit(FrappeTestCase):
    """Credit limit validation."""

    def setUp(self):
        _make_branch()
        # Enable credit limit enforcement for these tests
        frappe.db.set_single_value("Merchant Settings", "enforce_customer_credit_limit", 1)

    def tearDown(self):
        frappe.db.rollback()

    def test_plan_within_limit_allowed(self):
        customer = _make_customer(credit_limit=2000)
        plan = _make_plan(customer, principal=1000)
        self.assertTrue(plan.name)

    def test_plan_exceeding_limit_blocked(self):
        customer = _make_customer(credit_limit=500)
        with self.assertRaises(frappe.exceptions.ValidationError):
            _make_plan(customer, principal=1000)

    def test_draft_plan_reduces_available_limit(self):
        """A second draft plan must count the first one's principal."""
        customer = _make_customer(credit_limit=1500)
        _make_plan(customer, principal=800)   # first draft
        with self.assertRaises(frappe.exceptions.ValidationError):
            _make_plan(customer, principal=800)   # total 1600 > 1500

    def test_unlimited_customer_passes(self):
        """credit_limit=0 means unlimited."""
        customer = _make_customer(credit_limit=0)
        plan = _make_plan(customer, principal=99999)
        self.assertTrue(plan.name)


class TestPaymentAllocation(FrappeTestCase):
    """Payment → schedule allocation."""

    def setUp(self):
        _make_branch()
        self.customer = _make_customer(credit_limit=0)
        self.plan = _make_plan(self.customer, principal=1200, months=6, interest_rate=0)

    def tearDown(self):
        frappe.db.rollback()

    def test_single_installment_marked_paid(self):
        installment_amount = flt(self.plan.installment_amount)
        _make_payment(self.customer, self.plan.name, installment_amount)

        self.plan.reload()
        paid = [r for r in self.plan.schedule if r.status == "Оплачен"]
        self.assertEqual(len(paid), 1)

    def test_remaining_balance_decreases(self):
        installment_amount = flt(self.plan.installment_amount)
        original_balance = flt(self.plan.remaining_balance)
        _make_payment(self.customer, self.plan.name, installment_amount)

        self.plan.reload()
        new_balance = flt(self.plan.remaining_balance)
        self.assertAlmostEqual(new_balance, original_balance - installment_amount, places=2)

    def test_full_payoff_marks_plan_complete(self):
        total = flt(self.plan.total_amount)
        _make_payment(self.customer, self.plan.name, total)

        self.plan.reload()
        unpaid = [
            r for r in self.plan.schedule
            if r.status not in ("Оплачен",)
        ]
        self.assertEqual(len(unpaid), 0)
        self.assertAlmostEqual(flt(self.plan.remaining_balance), 0.0, places=2)

    def test_overpayment_stored(self):
        total = flt(self.plan.total_amount)
        overpay = total + 50
        pt = _make_payment(self.customer, self.plan.name, overpay)
        pt.reload()
        self.assertGreater(flt(pt.overpayment_amount), 0)

    def test_payment_idempotent_on_double_sync(self):
        """Calling _sync_payment_to_cashbox twice must not double-count."""
        from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
            _sync_payment_to_cashbox,
        )

        pt = _make_payment(self.customer, self.plan.name, flt(self.plan.installment_amount))
        cashbox_name = frappe.db.get_value(
            "Cashbox Transaction", {"reference_name": pt.name}, "parent"
        )
        if cashbox_name:
            cashbox = frappe.get_doc("Cashbox", cashbox_name)
            count_before = len(
                [t for t in cashbox.transactions if t.reference_name == pt.name]
            )
            _sync_payment_to_cashbox(pt)
            cashbox.reload()
            count_after = len(
                [t for t in cashbox.transactions if t.reference_name == pt.name]
            )
            self.assertEqual(count_before, count_after)


class TestCashHandoverTotals(FrappeTestCase):
    """Cash Handover validate / totals calculation."""

    def tearDown(self):
        frappe.db.rollback()

    def _make_cashboxes(self):
        branch = _make_branch(branch_name="Handover Branch", city="Handover City")
        user = frappe.session.user
        from_cb = frappe.get_doc(
            {
                "doctype": "Cashbox",
                "cashbox_name": "Cashier Box Test",
                "branch": branch,
                "responsible_user": user,
                "status": "Открыта",
                "is_master": 0,
            }
        ).insert(ignore_permissions=True)
        to_cb = frappe.get_doc(
            {
                "doctype": "Cashbox",
                "cashbox_name": "Manager Box Test",
                "branch": branch,
                "responsible_user": user,
                "status": "Открыта",
                "is_master": 1,
            }
        ).insert(ignore_permissions=True)
        return from_cb.name, to_cb.name

    def test_totals_calculated_on_validate(self):
        from_cb, to_cb = self._make_cashboxes()
        handover = frappe.get_doc(
            {
                "doctype": "Cash Handover",
                "from_cashbox": from_cb,
                "to_cashbox": to_cb,
                "transferred_by": frappe.session.user,
                "transfer_date": today(),
                "lines": [
                    {"payment_method": "Наличные USD", "currency": "USD", "amount": 100},
                    {"payment_method": "Карта",        "currency": "USD", "amount": 50},
                ],
            }
        )
        handover.insert(ignore_permissions=True)
        self.assertAlmostEqual(flt(handover.total_usd), 150.0, places=2)

    def test_confirmed_amount_defaults_to_amount(self):
        from_cb, to_cb = self._make_cashboxes()
        handover = frappe.get_doc(
            {
                "doctype": "Cash Handover",
                "from_cashbox": from_cb,
                "to_cashbox": to_cb,
                "transferred_by": frappe.session.user,
                "transfer_date": today(),
                "lines": [
                    {"payment_method": "Наличные USD", "currency": "USD", "amount": 200},
                ],
            }
        )
        handover.insert(ignore_permissions=True)
        line = handover.lines[0]
        self.assertAlmostEqual(flt(line.confirmed_amount), 200.0, places=2)
        self.assertAlmostEqual(flt(line.discrepancy), 0.0, places=2)

    def test_discrepancy_calculated_on_adjustment(self):
        from_cb, to_cb = self._make_cashboxes()
        handover = frappe.get_doc(
            {
                "doctype": "Cash Handover",
                "from_cashbox": from_cb,
                "to_cashbox": to_cb,
                "transferred_by": frappe.session.user,
                "transfer_date": today(),
                "lines": [
                    {
                        "payment_method": "Наличные USD",
                        "currency": "USD",
                        "amount": 200,
                        "confirmed_amount": 190,
                    }
                ],
            }
        )
        handover.insert(ignore_permissions=True)
        line = handover.lines[0]
        self.assertAlmostEqual(flt(line.discrepancy), 10.0, places=2)
        self.assertAlmostEqual(flt(handover.total_discrepancy), 10.0, places=2)
