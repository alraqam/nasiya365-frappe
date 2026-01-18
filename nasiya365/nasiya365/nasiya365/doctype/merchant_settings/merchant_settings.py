"""
Merchant Settings DocType Controller
Single DocType for tenant-specific settings
"""

import frappe
from frappe.model.document import Document


class MerchantSettings(Document):
    def validate(self):
        self.validate_installment_months()
        self.validate_percentages()
    
    def validate_installment_months(self):
        if self.min_installment_months and self.max_installment_months:
            if self.min_installment_months > self.max_installment_months:
                frappe.throw("Minimum installment months cannot be greater than maximum")
    
    def validate_percentages(self):
        if self.default_interest_rate and self.default_interest_rate > 100:
            frappe.throw("Interest rate cannot be more than 100%")
        
        if self.default_down_payment_percent and self.default_down_payment_percent > 100:
            frappe.throw("Down payment percentage cannot be more than 100%")
        
        if self.late_fee_percentage and self.late_fee_percentage > 50:
            frappe.msgprint("Late fee percentage is very high. Please verify.")
