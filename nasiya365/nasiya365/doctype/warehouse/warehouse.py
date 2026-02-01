"""
Warehouse DocType Controller
"""

import frappe
from frappe.model.document import Document


class Warehouse(Document):
    def validate(self):
        self.set_warehouse_code()
        self.validate_default()
    
    def set_warehouse_code(self):
        """Auto-generate warehouse code if not set"""
        if not self.warehouse_code:
            branch = frappe.get_doc("Branch", self.branch)
            branch_code = branch.branch_code or "XX"
            count = frappe.db.count("Warehouse", {"branch": self.branch}) + 1
            self.warehouse_code = f"{branch_code}-WH{count:02d}"
    
    def validate_default(self):
        """Ensure only one default warehouse per branch"""
        if self.is_default:
            frappe.db.set_value(
                "Warehouse",
                {"branch": self.branch, "name": ("!=", self.name)},
                "is_default",
                0
            )
