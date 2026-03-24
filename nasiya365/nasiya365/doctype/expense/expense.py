"""
Expense DocType Controller
Tracks branch-level operational expenses with optional cashbox deduction
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class Expense(Document):
    def before_insert(self):
        if not self.paid_by:
            self.paid_by = frappe.session.user

    def validate(self):
        if flt(self.amount) <= 0:
            frappe.throw(_("Сумма расхода должна быть больше нуля"))

    def on_submit(self):
        self.status = "Оплачен"
        self.db_update()

        if self.cashbox:
            self._deduct_from_cashbox()

    def on_cancel(self):
        self.status = "Отменен"
        self.db_update()

        if self.cashbox:
            self._restore_cashbox()

    def _deduct_from_cashbox(self):
        """Append an expense transaction to the linked Cashbox"""
        cashbox = frappe.get_doc("Cashbox", self.cashbox)
        cashbox.append("transactions", {
            "transaction_type": "Расход",
            "amount": self.amount,
            "category": "Расход",
            "reference_doctype": "Expense",
            "reference_name": self.name,
            "notes": self.title
        })
        cashbox.save(ignore_permissions=True)

    def _restore_cashbox(self):
        """Remove the expense transaction from the linked Cashbox on cancel"""
        cashbox = frappe.get_doc("Cashbox", self.cashbox)
        cashbox.transactions = [
            t for t in cashbox.transactions
            if not (t.reference_doctype == "Expense" and t.reference_name == self.name)
        ]
        cashbox.save(ignore_permissions=True)
