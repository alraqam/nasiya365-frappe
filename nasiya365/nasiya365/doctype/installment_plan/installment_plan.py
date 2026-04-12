"""
Installment Plan DocType Controller
Core BNPL logic for managing customer installment plans
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, add_to_date, cint, getdate, today, flt

from nasiya365.utils.credit import is_customer_credit_limit_enforced
from decimal import Decimal


def _installment_row_accepts_payment(row) -> bool:
    """Treat blank / legacy English status as open (same idea as bnpl_dashboard _OPEN_SCHEDULE_STATUSES)."""
    st = (row.status or "").strip()
    if not st or st == "Pending":
        return True
    return st in ("Ожидает", "Просрочен", "Частично")


def _schedule_has_payment_activity(schedule) -> bool:
    """If any installment (including row 0 down payment) was paid, never rebuild the child table."""
    for row in schedule or []:
        if flt(row.paid_amount) > 0:
            return True
        st = (row.status or "").strip()
        if st in ("Оплачен", "Частично"):
            return True
    return False


def _normalize_frequency(freq):
    """Map EN / RU / bilingual Select values to the canonical bilingual options."""
    if not freq:
        return "Ежемесячно (Monthly)"
    key = (freq or "").strip()

    CANONICAL = {
        "Ежемесячно": "Ежемесячно (Monthly)",
        "Еженедельно": "Еженедельно (Weekly)",
        "Раз в две недели": "Раз в две недели (Biweekly)",
        "Ежемесячно (Monthly)": "Ежемесячно (Monthly)",
        "Еженедельно (Weekly)": "Еженедельно (Weekly)",
        "Раз в две недели (Biweekly)": "Раз в две недели (Biweekly)",
        "Monthly": "Ежемесячно (Monthly)",
        "Weekly": "Еженедельно (Weekly)",
        "Biweekly": "Раз в две недели (Biweekly)",
    }
    if key in CANONICAL:
        return CANONICAL[key]
    base = key.split("(")[0].strip()
    return CANONICAL.get(base, "Ежемесячно (Monthly)")


class InstallmentPlan(Document):
    def validate(self):
        if frappe.flags.in_import:
            return

        # Payment-driven save: skip stock / new-plan checks and schedule regeneration so
        # cashier payments never block allocation or wipe the just-updated schedule rows.
        if frappe.flags.get("nasiya_plan_allocating_payment"):
            self.frequency = _normalize_frequency(self.frequency)
            # Recalculate totals directly from the (already-mutated) schedule rows.
            # Do NOT call calculate_amounts() here — it would overwrite remaining_balance
            # using the pre-payment paid_amount before the sum below runs.
            # Do NOT call generate_schedule() — it must not rebuild a schedule that
            # already has payment allocations on its rows.
            if self.schedule:
                self.paid_amount = sum(flt(s.paid_amount) for s in self.schedule)
                self.remaining_balance = flt(self.total_amount) - flt(self.paid_amount)
            self.update_progress()
            self.set_contract_fields_from_plan()
            return

        self.frequency = _normalize_frequency(self.frequency)

        self.validate_customer_limit()
        self.validate_stock_entry_for_bnpl()
        self._warn_structural_change_on_paid_plan()
        self.calculate_amounts()
        self.generate_schedule()
        if self.schedule:
            self.paid_amount = sum(flt(s.paid_amount) for s in self.schedule)
            self.remaining_balance = flt(self.total_amount) - flt(self.paid_amount)
            self._validate_schedule_sum()
        self.update_progress()
        self.set_contract_fields_from_plan()

    def set_contract_fields_from_plan(self):
        """
        b3-merge compatibility: Contract data now lives on Installment Plan.
        We compute the contract-related financial fields from the schedule.
        """
        # Set/refresh contract number
        if hasattr(self, "contract_number") and not self.contract_number:
            self.contract_number = self.name

        # Populate product model + IMEI from Stock Entry (preferred) or Sales Order (legacy / import)
        if hasattr(self, "product_name") and hasattr(self, "imei"):
            if (not self.product_name or not self.imei) and getattr(self, "stock_entry", None):
                sei = frappe.get_all(
                    "Stock Entry Item",
                    filters={"parent": self.stock_entry},
                    fields=["product", "serial_no"],
                    order_by="idx asc",
                    limit=1,
                )
                if sei:
                    row = sei[0]
                    if not self.product_name and row.get("product"):
                        self.product_name = frappe.db.get_value(
                            "Product", row["product"], "product_name"
                        )
                    if not self.imei and row.get("serial_no"):
                        full_imei = (row["serial_no"] or "").strip()
                        self.imei = full_imei[-6:] if len(full_imei) >= 6 else full_imei
            elif (not self.product_name or not self.imei) and self.sales_order:
                items = frappe.get_all(
                    "Sales Order Item",
                    filters={"parent": self.sales_order},
                    fields=["product_name", "imei"],
                    order_by="idx asc",
                    limit=1,
                )
                if items:
                    if not self.product_name and items[0].get("product_name"):
                        self.product_name = items[0]["product_name"]
                    if not self.imei and items[0].get("imei"):
                        full_imei = (items[0]["imei"] or "").strip()
                        self.imei = full_imei[-6:] if len(full_imei) >= 6 else full_imei

        # Update contract status based on signatures (same rule as Contract DocType)
        if hasattr(self, "contract_status"):
            if self.signed_by_customer and self.signed_by_merchant:
                if self.contract_status == "Черновик":
                    self.contract_status = "Подписан"

        # Financial summary (same rule as Contract.set_financial_summary)
        if hasattr(self, "total_debt"):
            self.total_debt = flt(getattr(self, "remaining_balance", 0))
        if hasattr(self, "monthly_payment"):
            self.monthly_payment = flt(getattr(self, "installment_amount", 0))

        if hasattr(self, "debt_today"):
            debt_today = 0
            if getattr(self, "schedule", None):
                for s in self.schedule:
                    if not s.due_date or getdate(s.due_date) > getdate(today()):
                        continue
                    if s.status in ["Ожидает", "Просрочен", "Частично"]:
                        debt_today += flt(s.amount) - flt(s.paid_amount)
            self.debt_today = debt_today
    
    def before_insert(self):
        self.created_by = frappe.session.user
    
    def on_submit(self):
        self.update_customer_limit()
        self.create_contract()
    
    def on_cancel(self):
        self.release_customer_limit()
        self.add_comment(
            "Info",
            _("План отменён пользователем {0}. Остаток на момент отмены: {1} USD.").format(
                frappe.session.user,
                flt(self.remaining_balance, 2),
            ),
        )
    
    def validate_stock_entry_for_bnpl(self):
        if not getattr(self, "stock_entry", None):
            return
        from nasiya365.api.bnpl_dashboard import assert_stock_entry_available_for_installment_plan

        assert_stock_entry_available_for_installment_plan(self.stock_entry, self.name or "")

    def validate_customer_limit(self):
        """Check customer status; optional cap when Merchant Settings + profile credit_limit apply.

        Accounts for other draft plans to prevent concurrent-draft bypass of the credit limit.
        """
        customer = frappe.get_doc("Customer Profile", self.customer)

        if customer.status != "Активный":
            frappe.throw(_("Клиент не активен"))

        if not is_customer_credit_limit_enforced():
            return
        # credit_limit <= 0 = без лимита
        if flt(customer.credit_limit) <= 0:
            return

        # Only enforce on new plans or when principal_amount changes on a still-draft plan.
        is_draft = self.docstatus == 0
        if not self.is_new() and not is_draft:
            return
        if not self.is_new() and is_draft and not self.has_value_changed("principal_amount"):
            return

        # Refresh debt from submitted plans
        customer.update_statistics()

        # Sum principal of OTHER draft plans for this customer (concurrent-draft protection)
        other_draft_principal = flt(frappe.db.sql(
            """
            SELECT COALESCE(SUM(principal_amount), 0)
            FROM `tabInstallment Plan`
            WHERE customer = %s
              AND docstatus = 0
              AND name != %s
            """,
            (self.customer, self.name or "__new__"),
        )[0][0])

        effective_available = flt(customer.available_limit) - other_draft_principal

        if flt(self.principal_amount) > effective_available:
            msg = _("Запрашиваемая сумма {0} превышает доступный кредитный лимит {1}").format(
                frappe.format_value(self.principal_amount, {"fieldtype": "Currency"}),
                frappe.format_value(max(0.0, effective_available), {"fieldtype": "Currency"}),
            )
            if other_draft_principal > 0:
                msg += " " + _("(учтено {0} по незакрытым черновикам)").format(
                    frappe.format_value(other_draft_principal, {"fieldtype": "Currency"})
                )
            frappe.throw(msg)
    
    def _warn_structural_change_on_paid_plan(self):
        """Warn when financial fields change on a plan that already has payment history."""
        if self.is_new():
            return
        if not _schedule_has_payment_activity(self.schedule):
            return
        structural = ("principal_amount", "interest_rate", "down_payment")
        changed = [f for f in structural if self.has_value_changed(f)]
        if changed:
            labels = {"principal_amount": "Сумма", "interest_rate": "Ставка", "down_payment": "Первоначальный взнос"}
            changed_labels = ", ".join(labels.get(f, f) for f in changed)
            frappe.msgprint(
                _("Внимание: изменены поля ({0}) на плане с уже существующими оплатами. "
                  "Суммы взносов в графике не будут пересчитаны автоматически.").format(changed_labels),
                indicator="orange",
                alert=True,
            )

    def calculate_amounts(self):
        """Calculate totals.
        total_amount = down_payment + financed_amount + total_interest
                     = principal + total_interest
        installment_amount covers only the financed portion (not the down payment).
        """
        principal = flt(self.principal_amount)
        down_payment = flt(self.down_payment)
        interest_rate = flt(self.interest_rate) / 100
        num_installments = int(self.number_of_installments)

        self.financed_amount = principal - down_payment

        self.total_interest = self.financed_amount * interest_rate * num_installments

        financed_total = self.financed_amount + self.total_interest
        # Include down payment in total_amount only when schedule has a row 0 (new-style plans).
        has_dp_row = any(cint(s.installment_number) == 0 for s in (self.schedule or []))
        self.total_amount = financed_total + (down_payment if has_dp_row else 0)

        if num_installments > 0:
            self.installment_amount = financed_total / num_installments
        else:
            self.installment_amount = financed_total

        self.remaining_balance = self.total_amount - flt(self.paid_amount)
    
    def _next_schedule_date(self, from_date):
        """Return the next due date based on frequency."""
        freq = (self.frequency or "")
        if "Еженедельно" in freq:
            return add_to_date(from_date, weeks=1)
        elif "две недели" in freq:
            return add_to_date(from_date, weeks=2)
        else:
            return add_months(from_date, 1)

    def generate_schedule(self):
        """Generate or extend installment schedule.

        - No payment activity: full rebuild.
        - Payment activity + regular_count < num: append new rows (plan extension).
        - Payment activity + regular_count > num: throw — cannot shrink a paid plan.
        - Payment activity + regular_count == num: no-op.
        """
        num = cint(self.number_of_installments)
        if num <= 0:
            return

        regular_rows = [s for s in (self.schedule or []) if cint(s.installment_number) > 0]
        regular_count = len(regular_rows)
        has_activity = _schedule_has_payment_activity(self.schedule)

        if has_activity:
            if regular_count == num:
                return  # nothing to do
            if regular_count > num:
                frappe.throw(
                    _("Нельзя уменьшить количество взносов с {0} до {1}: "
                      "по плану уже были произведены оплаты. "
                      "Для изменения условий отмените план и создайте новый.").format(
                        regular_count, num
                    )
                )
            # regular_count < num → extend: append (num - regular_count) new rows
            last_row = max(regular_rows, key=lambda s: cint(s.installment_number))
            current_date = getdate(last_row.due_date)
            new_row_amount = round(self.installment_amount, 2)
            for i in range(regular_count, num):
                current_date = self._next_schedule_date(current_date)
                self.append("schedule", {
                    "installment_number": i + 1,
                    "due_date": current_date,
                    "amount": new_row_amount,
                    "status": "Ожидает",
                    "paid_amount": 0,
                })
            if self.schedule:
                last_reg = max(
                    (s for s in self.schedule if cint(s.installment_number) > 0),
                    key=lambda s: cint(s.installment_number),
                )
                self.end_date = last_reg.due_date
            return

        # No payment activity — full rebuild
        if not self.schedule or regular_count != num:
            self.schedule = []

            # Row 0 — down payment (unpaid, due on start_date)
            down = flt(self.down_payment)
            if down > 0:
                self.append("schedule", {
                    "installment_number": 0,
                    "due_date": self.start_date,
                    "amount": down,
                    "status": "Ожидает",
                    "paid_amount": 0,
                })

            current_date = getdate(self.start_date)
            financed_total = flt(self.financed_amount) + flt(self.total_interest)
            allocated = 0.0
            for i in range(num):
                if i > 0:
                    current_date = self._next_schedule_date(current_date)

                # Last row absorbs rounding remainder so sum(schedule) == total_amount exactly
                if i == num - 1:
                    row_amount = round(financed_total - allocated, 2)
                else:
                    row_amount = round(self.installment_amount, 2)
                    allocated += row_amount

                self.append("schedule", {
                    "installment_number": i + 1,
                    "due_date": current_date,
                    "amount": row_amount,
                    "status": "Ожидает",
                    "paid_amount": 0,
                })

            if self.schedule:
                self.end_date = self.schedule[-1].due_date
    
    def _validate_schedule_sum(self):
        """Warn if schedule row amounts don't add up to total_amount (rounding drift on old plans)."""
        if not self.schedule:
            return
        schedule_sum = round(sum(flt(s.amount) for s in self.schedule), 2)
        total = round(flt(self.total_amount), 2)
        diff = abs(schedule_sum - total)
        if diff > 0.01:
            frappe.msgprint(
                _("График платежей: сумма строк ({0}) не совпадает с итоговой суммой ({1}). "
                  "Разница: {2}. Пересохраните план для автоматического исправления.").format(
                    schedule_sum, total, round(diff, 4)
                ),
                indicator="orange",
                alert=True,
            )

    def update_progress(self):
        """Update progress counters."""
        if self.schedule:
            self.paid_installments = len([s for s in self.schedule if s.status == "Оплачен"])
            self.overdue_installments = len([s for s in self.schedule if s.status == "Просрочен"])
    
    def update_customer_limit(self):
        """Reduce customer's available limit when plan is submitted"""
        customer = frappe.get_doc("Customer Profile", self.customer)
        customer.update_statistics()
        customer.db_update()
    
    def release_customer_limit(self):
        """Restore customer's available limit when plan is cancelled"""
        customer = frappe.get_doc("Customer Profile", self.customer)
        customer.update_statistics()
        customer.db_update()
    
    def create_contract(self):
        """Auto-create contract document when plan is submitted"""
        # Will be implemented when Contract DocType is ready
        pass
    
    def apply_payment(self, amount, payment_transaction=None):
        """
        Apply a payment to this installment plan
        Automatically allocates to oldest pending/overdue installments first
        """
        if not self.schedule:
            frappe.throw(
                _("У плана нет строк графика — сохраните план с заполненным графиком, затем повторите оплату.")
            )

        remaining_payment = flt(amount)
        
        # Sort schedule by due date (normalize: DB/doc may use date or str)
        sorted_schedule = sorted(
            self.schedule,
            key=lambda x: getdate(x.due_date) if x.due_date else getdate("1900-01-01"),
        )
        
        for installment in sorted_schedule:
            if _installment_row_accepts_payment(installment):
                due_amount = flt(installment.amount) - flt(installment.paid_amount)
                
                if remaining_payment >= due_amount:
                    # Full payment for this installment
                    installment.paid_amount = installment.amount
                    installment.status = "Оплачен"
                    installment.paid_date = today()
                    if payment_transaction and hasattr(installment, "payment_transaction"):
                        installment.payment_transaction = payment_transaction
                    remaining_payment -= due_amount
                elif remaining_payment > 0:
                    # Partial payment
                    installment.paid_amount = flt(installment.paid_amount) + remaining_payment
                    installment.status = "Частично"
                    if payment_transaction and hasattr(installment, "payment_transaction"):
                        installment.payment_transaction = payment_transaction
                    remaining_payment = 0
                
                if remaining_payment <= 0:
                    break
        
        # Update totals
        self.paid_amount = sum(flt(s.paid_amount) for s in self.schedule)
        self.remaining_balance = self.total_amount - self.paid_amount
        self.update_progress()

        # Check if all rows (including down payment) are paid
        if all(s.status == "Оплачен" for s in self.schedule):
            self.status = "Завершен"
        
        # Only submitted docs need this flag; draft plans (docstatus 0) can misbehave if it is always set.
        if getattr(self, "docstatus", 0) == 1:
            self.flags.ignore_validate_update_after_submit = True
        # Cashiers can create payments but often have no Write on Installment Plan; allocation must still persist.
        frappe.flags.nasiya_plan_allocating_payment = True
        try:
            self.save(ignore_permissions=True)
        finally:
            frappe.flags.nasiya_plan_allocating_payment = False
        
        # Update customer statistics (must not roll back a successful plan save)
        try:
            frappe.get_doc("Customer Profile", self.customer, ignore_permissions=True).update_statistics()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Installment Plan: update_statistics after payment")
        
        return remaining_payment  # Return any excess payment


@frappe.whitelist()
def get_stock_entry_details(stock_entry, installment_plan=None):
    """Backward-compatible alias; desk calls api.bnpl_dashboard.get_stock_entry_details_for_installment_plan."""
    from nasiya365.api.bnpl_dashboard import get_stock_entry_details_for_installment_plan

    return get_stock_entry_details_for_installment_plan(stock_entry, installment_plan)


@frappe.whitelist()
def get_sales_order_details(sales_order):
    """Return SO total and first item's product_name + imei for auto-fill (legacy)."""
    so = frappe.get_doc("Sales Order", sales_order)
    result = {
        "total_amount": flt(so.total_amount),
        "customer": so.customer,
        "product_name": None,
        "imei": None,
    }
    if so.items:
        first = so.items[0]
        result["product_name"] = first.product_name or (
            frappe.db.get_value("Product", first.product, "product_name") if first.product else None
        )
        result["imei"] = first.imei
    return result


@frappe.whitelist()
def calculate_installment_preview(principal, down_payment, interest_rate, num_installments, frequency, start_date):
    """
    API endpoint to preview installment calculation before creating plan
    """
    return _build_installment_preview(
        principal, down_payment, interest_rate, num_installments, frequency, start_date
    )


def _build_installment_preview(principal, down_payment, interest_rate, num_installments, frequency, start_date):
    principal = flt(principal)
    down_payment = flt(down_payment)
    interest_rate = flt(interest_rate) / 100
    num_installments = int(num_installments)
    frequency = _normalize_frequency(frequency)

    financed = principal - down_payment
    total_interest = financed * interest_rate * num_installments
    financed_total = financed + total_interest
    total_amount = financed_total + down_payment  # includes down payment
    installment_amount = financed_total / num_installments if num_installments > 0 else financed_total

    schedule = []
    current_date = getdate(start_date)

    # Row 0 — down payment (unpaid, due on start_date)
    if down_payment > 0:
        schedule.append({
            "installment_number": 0,
            "due_date": str(current_date),
            "amount": down_payment,
            "status": "Ожидает",
            "paid_amount": 0,
        })

    for i in range(num_installments):
        if i > 0:
            if "Еженедельно" in frequency:
                current_date = add_to_date(current_date, weeks=1)
            elif "две недели" in frequency:
                current_date = add_to_date(current_date, weeks=2)
            else:
                current_date = add_months(current_date, 1)

        schedule.append({
            "installment_number": i + 1,
            "due_date": str(current_date),
            "amount": installment_amount,
        })

    return {
        "financed_amount": financed,
        "total_interest": total_interest,
        "total_amount": total_amount,
        "installment_amount": installment_amount,
        "end_date": str(current_date) if schedule else None,
        "schedule": schedule,
        "frequency": frequency,
    }


@frappe.whitelist()
def generate_installment_schedule(
    principal, down_payment, interest_rate, num_installments, frequency, start_date
):
    """Operator UI: same payload as preview; explicit name for client calls."""
    return _build_installment_preview(
        principal, down_payment, interest_rate, num_installments, frequency, start_date
    )


@frappe.whitelist()
def compare_installment_terms(principal, down_payment, interest_rate, frequency, start_date):
    """Single round-trip: 6 / 9 / 12 month previews for operator simulator."""
    return [
        _build_installment_preview(principal, down_payment, interest_rate, n, frequency, start_date)
        for n in (6, 9, 12)
    ]


@frappe.whitelist()
def send_installment_plan_otp(customer):
    """Stub: wire SMS/gateway later."""
    if not customer:
        frappe.throw(_("Клиент обязателен"))
    return {
        "ok": True,
        "message": _("OTP: интеграция в разработке (клиент {0})").format(customer),
    }
