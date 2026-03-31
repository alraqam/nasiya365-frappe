"""
Installment Plan DocType Controller
Core BNPL logic for managing customer installment plans
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, add_to_date, getdate, today, flt
from decimal import Decimal


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

        self.frequency = _normalize_frequency(self.frequency)

        self.validate_customer_limit()
        self.calculate_amounts()
        self.generate_schedule()
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

        # Populate product model + IMEI from Sales Order
        if hasattr(self, "product_name") and hasattr(self, "imei") and self.sales_order:
            if not self.product_name or not self.imei:
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
                    if not s.due_date or s.due_date > today():
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
    
    def validate_customer_limit(self):
        """Check if customer has sufficient credit limit"""
        if self.is_new():
            customer = frappe.get_doc("Customer Profile", self.customer)
            
            if customer.status != "Активный":
                frappe.throw(_("Клиент не активен"))
            
            if self.principal_amount > customer.available_limit:
                frappe.throw(
                    _("Запрашиваемая сумма {0} превышает доступный кредитный лимит {1}").format(
                        frappe.format_value(self.principal_amount, {"fieldtype": "Currency"}),
                        frappe.format_value(customer.available_limit, {"fieldtype": "Currency"})
                    )
                )
    
    def calculate_amounts(self):
        """Calculate total interest 、, financed amount, and installment amount"""
        principal = flt(self.principal_amount)
        down_payment = flt(self.down_payment)
        interest_rate = flt(self.interest_rate) / 100  # Monthly rate
        num_installments = int(self.number_of_installments)
        
        # Financed amount is principal minus down payment
        self.financed_amount = principal - down_payment
        
        # Calculate total interest (simple interest for now)
        self.total_interest = self.financed_amount * interest_rate * num_installments
        
        # Total amount including interest
        self.total_amount = self.financed_amount + self.total_interest
        
        # Equal installment amount
        if num_installments > 0:
            self.installment_amount = self.total_amount / num_installments
        else:
            self.installment_amount = self.total_amount
        
        # Calculate remaining balance
        self.remaining_balance = self.total_amount - flt(self.paid_amount)
    
    def generate_schedule(self):
        """Generate installment schedule based on frequency"""
        if not self.schedule or len(self.schedule) != self.number_of_installments:
            self.schedule = []
            
            current_date = getdate(self.start_date)
            
            for i in range(self.number_of_installments):
                if i > 0:
                    freq = (self.frequency or "")
                    if "Еженедельно" in freq:
                        current_date = add_to_date(current_date, weeks=1)
                    elif "две недели" in freq:
                        current_date = add_to_date(current_date, weeks=2)
                    else:
                        current_date = add_months(current_date, 1)
                
                self.append("schedule", {
                    "installment_number": i + 1,
                    "due_date": current_date,
                    "amount": self.installment_amount,
                    "status": "Ожидает",
                    "paid_amount": 0
                })
            
            # Set end date
            if self.schedule:
                self.end_date = self.schedule[-1].due_date
    
    def update_progress(self):
        """Update progress counters"""
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
        remaining_payment = flt(amount)
        
        # Sort schedule by due date
        sorted_schedule = sorted(self.schedule, key=lambda x: x.due_date)
        
        for installment in sorted_schedule:
            if installment.status in ["Ожидает", "Просрочен", "Частично"]:
                due_amount = flt(installment.amount) - flt(installment.paid_amount)
                
                if remaining_payment >= due_amount:
                    # Full payment for this installment
                    installment.paid_amount = installment.amount
                    installment.status = "Оплачен"
                    installment.paid_date = today()
                    remaining_payment -= due_amount
                elif remaining_payment > 0:
                    # Partial payment
                    installment.paid_amount = flt(installment.paid_amount) + remaining_payment
                    installment.status = "Частично"
                    remaining_payment = 0
                
                if remaining_payment <= 0:
                    break
        
        # Update totals
        self.paid_amount = sum(flt(s.paid_amount) for s in self.schedule)
        self.remaining_balance = self.total_amount - self.paid_amount
        self.update_progress()
        
        # Check if plan is completed
        if all(s.status == "Оплачен" for s in self.schedule):
            self.status = "Завершен"
        
        self.flags.ignore_validate_update_after_submit = True
        self.save()
        
        # Update customer statistics
        frappe.get_doc("Customer Profile", self.customer).update_statistics()
        
        return remaining_payment  # Return any excess payment


@frappe.whitelist()
def get_sales_order_details(sales_order):
    """Return SO total and first item's product_name + imei for auto-fill."""
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
    total_amount = financed + total_interest
    installment_amount = total_amount / num_installments if num_installments > 0 else total_amount

    schedule = []
    current_date = getdate(start_date)

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
            "amount": installment_amount
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
