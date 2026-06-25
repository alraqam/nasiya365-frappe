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
        self.validate_shareholders()

    def validate_shareholders(self):
        """When using fixed-percentage split, active shares should sum to 100%."""
        if (self.shareholder_split_model or "Фиксированный процент") != "Фиксированный процент":
            return
        active = [s for s in (self.shareholders or []) if s.is_active]
        if not active:
            return
        total = sum(frappe.utils.flt(s.share_percent) for s in active)
        if abs(total - 100) > 0.01:
            frappe.msgprint(
                frappe._("Сумма долей активных акционеров = {0}%. Обычно должна быть 100%.").format(
                    frappe.utils.flt(total, 2)
                ),
                indicator="orange",
                title=frappe._("Проверьте доли акционеров"),
            )

    def validate_installment_months(self):
        if self.min_installment_months and self.max_installment_months:
            if self.min_installment_months > self.max_installment_months:
                frappe.throw("Minimum installment months cannot be greater than maximum")
    
    def validate_percentages(self):
        if self.default_interest_rate and self.default_interest_rate > 100:
            frappe.throw("Interest rate cannot be more than 100%")
        
        if self.default_down_payment_percent and self.default_down_payment_percent > 100:
            frappe.throw("Down payment percentage cannot be more than 100%")
