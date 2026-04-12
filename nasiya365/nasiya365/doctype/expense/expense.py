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

        if self.cashbox:
            status = frappe.db.get_value("Cashbox", self.cashbox, "status")
            if not status:
                frappe.throw(_("Касса {0} не найдена").format(self.cashbox))
            if status == "Закрыта":
                frappe.throw(
                    _("Касса {0} уже закрыта. Выберите открытую кассу или снимите привязку.").format(
                        self.cashbox
                    )
                )

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
        """Append an expense transaction to the linked Cashbox."""
        status = frappe.db.get_value("Cashbox", self.cashbox, "status")
        if not status:
            frappe.log_error(
                f"Expense {self.name}: cashbox {self.cashbox} not found during deduct",
                "Expense: cashbox missing",
            )
            return
        if status == "Закрыта":
            frappe.throw(
                _("Невозможно списать расход: касса {0} уже закрыта.").format(self.cashbox)
            )

        currency = (self.currency or "USD").strip().upper()
        payment_method = "Наличные UZS" if currency == "UZS" else "Наличные USD"
        exchange_rate = flt(getattr(self, "exchange_rate", 0))

        cashbox = frappe.get_doc("Cashbox", self.cashbox)
        cashbox.append("transactions", {
            "transaction_type": "Расход",
            "payment_method": payment_method,
            "currency": currency,
            "exchange_rate": exchange_rate,
            "amount": flt(self.amount),
            "category": "Расход",
            "reference_doctype": "Expense",
            "reference_name": self.name,
            "notes": self.title,
        })
        cashbox.save(ignore_permissions=True)

    def _restore_cashbox(self):
        """Remove the expense transaction from the linked Cashbox on cancel."""
        if not frappe.db.exists("Cashbox", self.cashbox):
            return
        cashbox = frappe.get_doc("Cashbox", self.cashbox)
        before = len(cashbox.transactions or [])
        cashbox.transactions = [
            t for t in (cashbox.transactions or [])
            if not (t.reference_doctype == "Expense" and t.reference_name == self.name)
        ]
        if len(cashbox.transactions) != before:
            cashbox.save(ignore_permissions=True)
