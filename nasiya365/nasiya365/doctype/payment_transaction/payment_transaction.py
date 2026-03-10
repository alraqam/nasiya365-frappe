"""
Payment Transaction DocType Controller
"""

import frappe
from frappe.model.document import Document


class PaymentTransaction(Document):
    def before_insert(self):
        if not self.received_by:
            self.received_by = frappe.session.user
            
    def after_insert(self):
        """
        Logic to run after a payment is inserted.
        This is referenced in hooks.py.
        """
        if self.reference_doctype == "Installment Plan" and self.reference_name:
            plan = frappe.get_doc("Installment Plan", self.reference_name)
            plan.apply_payment(self.amount, payment_transaction=self.name)
