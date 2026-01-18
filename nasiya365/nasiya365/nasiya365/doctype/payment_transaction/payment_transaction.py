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
        # Add payment processing logic here if needed
        pass
