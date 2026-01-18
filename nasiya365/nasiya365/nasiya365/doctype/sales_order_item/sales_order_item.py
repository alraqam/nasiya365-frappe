"""
Sales Order Item Child Table DocType
"""

from frappe.model.document import Document
from frappe.utils import flt


class SalesOrderItem(Document):
    def validate(self):
        self.calculate_amount()
    
    def calculate_amount(self):
        """Calculate line item amount"""
        subtotal = flt(self.quantity) * flt(self.unit_price)
        discount = (subtotal * flt(self.discount_percent)) / 100
        self.amount = subtotal - discount
