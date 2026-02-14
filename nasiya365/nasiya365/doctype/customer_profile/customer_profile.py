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
        self.validate_phone_numbers()
        self.validate_passport()
        self.validate_pinfl()
        self.validate_age()
        self.validate_passport_dates()
        self.sync_addresses()
        
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
