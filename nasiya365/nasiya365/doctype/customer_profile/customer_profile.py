"""
Customer Profile DocType Controller
Handles customer management with enhanced validation for personal info, addresses, and identity documents
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today, date_diff
import re


class CustomerProfile(Document):
    def validate(self):
        self.set_full_name()
        self.validate_phone_numbers()
        self.validate_passport()
        self.validate_pinfl()
        self.validate_age()
        self.validate_passport_dates()
        self.sync_addresses()
        self.update_available_limit()
        self.calculate_risk_profile()
    
    def set_full_name(self):
        """Set full_name from first_name and last_name"""
        parts = [self.first_name or "", self.last_name or ""]
        self.full_name = " ".join(p for p in parts if p).strip()
        
    def validate_phone_numbers(self):
        """Validate phone numbers table - ensure at least one exists and only one is primary"""
        if not self.phone_numbers or len(self.phone_numbers) == 0:
            frappe.throw(_("At least one phone number is required"))
        
        # Count primary phones
        primary_count = sum(1 for phone in self.phone_numbers if phone.is_primary)
        
        if primary_count == 0:
            frappe.throw(_("Please mark one phone number as Primary/Main"))
        
        if primary_count > 1:
            frappe.throw(_("Only one phone number can be marked as Primary/Main"))
    
    def validate_passport(self):
        """Validate passport format"""
        if self.passport_series:
            self.passport_series = self.passport_series.upper()
            if len(self.passport_series) != 2:
                frappe.throw(_("Passport series must be 2 letters"))
        
        if self.passport_number:
            if not self.passport_number.isdigit() or len(self.passport_number) > 15:
                frappe.throw(_("Passport number must be digits only, max 15 characters"))
    
    def validate_pinfl(self):
        """Validate PINFL (14 digits)"""
        if self.pinfl:
            # Remove spaces and dashes
            clean_pinfl = re.sub(r'[\s\-]', '', self.pinfl)
            
            if not re.match(r'^[0-9]{14}$', clean_pinfl):
                frappe.throw(_("PINFL must be exactly 14 digits"))
            
            self.pinfl = clean_pinfl
    
    def validate_age(self):
        """Validate customer is at least 18 years old"""
        if self.date_of_birth:
            age = date_diff(today(), self.date_of_birth) / 365
            if age < 18:
                frappe.throw(_("Customer must be at least 18 years old"))
            if age > 65:
                frappe.msgprint(_("Customer is over 65 years old. Manual approval may be required."))
    
    def validate_passport_dates(self):
        """Validate passport issue and expiry dates"""
        if self.passport_issue_date and self.passport_expiry_date:
            if getdate(self.passport_expiry_date) <= getdate(self.passport_issue_date):
                frappe.throw(_("Passport expiry date must be after issue date"))
    
    def sync_addresses(self):
        """If 'same as registration' is checked, copy registration address to current address"""
        if self.same_as_registration:
            self.current_address = self.registration_address
    
    def get_primary_phone(self):
        """Get the primary phone number"""
        for phone in self.phone_numbers:
            if phone.is_primary:
                return phone.phone_number
        return None

    def update_available_limit(self):
        """Calculate available limit = credit_limit - total_active_debt"""
        from frappe.utils import flt
        
        limit = flt(self.credit_limit)
        debt = flt(self.total_debt)
        self.available_limit = limit - debt
        
    def update_statistics(self):
        """Update active contracts count and total debt from Installment Plans"""
        from frappe.utils import flt
        
        plans = frappe.get_all(
            "Installment Plan",
            filters={
                "customer": self.name,
                "docstatus": 1,
                "status": ["!=", "Завершен"]
            },
            fields=["remaining_balance"]
        )
        
        self.active_contracts_count = len(plans)
        self.total_debt = sum(flt(p.remaining_balance) for p in plans)
        self.update_available_limit()
        self.calculate_risk_profile()
        self.db_update()

    def calculate_risk_profile(self):
        """Simple score based on overdue behavior and debt load."""
        from frappe.utils import flt

        overdue_rows = frappe.db.sql(
            """
            SELECT IFNULL(MAX(DATEDIFF(CURDATE(), due_date)), 0) AS max_delay,
                   COUNT(*) AS overdue_count
            FROM `tabInstallment Schedule`
            WHERE parent IN (
                SELECT name FROM `tabInstallment Plan`
                WHERE customer = %s AND docstatus = 1
            )
            AND status = 'Просрочен'
        """,
            (self.name,),
            as_dict=True,
        )
        max_delay = int((overdue_rows[0].max_delay if overdue_rows else 0) or 0)
        overdue_count = int((overdue_rows[0].overdue_count if overdue_rows else 0) or 0)

        utilization = 0
        if flt(self.credit_limit) > 0:
            utilization = (flt(self.total_debt) / flt(self.credit_limit)) * 100

        score = 100
        score -= min(max_delay, 45)
        score -= min(overdue_count * 8, 30)
        score -= min(int(utilization // 10) * 2, 20)
        score = max(0, min(100, score))

        if score >= 70:
            level = "Низкий"
        elif score >= 40:
            level = "Средний"
        else:
            level = "Высокий"

        self.last_payment_delay_days = max_delay
        self.risk_score = score
        self.risk_level = level


@frappe.whitelist()
def get_customer_by_phone(phone):
    """API endpoint to find customer by phone number"""
    # Normalize phone format
    clean_phone = re.sub(r'[\s\-]', '', phone)
    
    # Search in Customer Phone Number child table
    phone_records = frappe.db.get_all("Customer Phone Number", 
        filters={
            "phone_number": ["in", [phone, f"+998{clean_phone}", clean_phone]]
        },
        fields=["parent"]
    )
    
    if phone_records:
        customer_name = phone_records[0].parent
        customer = frappe.get_doc("Customer Profile", customer_name)
        return {
            "name": customer.name,
            "first_name": customer.first_name,
            "last_name": customer.last_name,
            "phone": customer.get_primary_phone(),
            "status": customer.status
        }
    
    return None
