import frappe
from frappe.model.document import Document


class CollectionLog(Document):
    def before_insert(self):
        if not self.logged_by:
            self.logged_by = frappe.session.user
