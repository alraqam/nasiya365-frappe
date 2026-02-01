"""
Branch DocType Controller
"""

import frappe
from frappe.model.document import Document


class Branch(Document):
    def validate(self):
        self.validate_branch_code()
        self.set_branch_code()
    
    def validate_branch_code(self):
        """Validate branch code format"""
        if self.branch_code:
            self.branch_code = self.branch_code.upper()
    
    def set_branch_code(self):
        """Auto-generate branch code if not set"""
        if not self.branch_code and self.city:
            # Take first 3 letters of city
            city_code = self.city[:3].upper()
            # Count existing branches in this city
            count = frappe.db.count("Branch", {"city": self.city}) + 1
            self.branch_code = f"{city_code}-{count:02d}"
