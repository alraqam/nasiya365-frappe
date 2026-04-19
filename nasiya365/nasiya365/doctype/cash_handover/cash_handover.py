import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CashHandover(Document):
    def validate(self):
        self._fill_confirmed_amount_defaults()
        self._calculate_totals()

    def before_submit(self):
        # Only record who confirmed; never mutate cashbox before submit succeeds
        # (otherwise a later submit failure leaves cashbox debited against a draft handover).
        self.confirmed_by = frappe.session.user

    def on_submit(self):
        # Wrap cashbox credit in a savepoint so a validation error on the cashbox
        # rolls back the handover submit instead of committing partial state.
        frappe.db.savepoint("nasiya_handover_on_submit")
        try:
            self._credit_manager_cashbox()
        except Exception:
            frappe.db.rollback(save_point="nasiya_handover_on_submit")
            raise
        self.add_comment(
            "Info",
            _("Инкассация подтверждена пользователем {0}. Принято: {1} USD / {2} UZS.").format(
                frappe.session.user,
                flt(self.confirmed_usd, 2),
                flt(self.confirmed_uzs, 0),
            ),
        )

    def on_cancel(self):
        self._debit_manager_cashbox()

    def _fill_confirmed_amount_defaults(self):
        """Set confirmed_amount = amount for lines where manager hasn't adjusted yet."""
        for line in self.lines or []:
            if not flt(line.confirmed_amount) and flt(line.amount) > 0:
                line.confirmed_amount = line.amount
            line.discrepancy = round(flt(line.amount) - flt(line.confirmed_amount), 2)

    def _calculate_totals(self):
        lines = self.lines or []
        # Claimed totals (from salesperson)
        self.total_usd = round(sum(flt(l.amount) for l in lines if (l.currency or "USD") == "USD"), 2)
        self.total_uzs = round(sum(flt(l.amount) for l in lines if (l.currency or "") == "UZS"), 0)
        # Confirmed totals (what manager accepted)
        self.confirmed_usd = round(sum(flt(l.confirmed_amount) for l in lines if (l.currency or "USD") == "USD"), 2)
        self.confirmed_uzs = round(sum(flt(l.confirmed_amount) for l in lines if (l.currency or "") == "UZS"), 0)
        # Discrepancy in USD (UZS discrepancies excluded — no exchange rate here)
        self.total_discrepancy = round(self.total_usd - self.confirmed_usd, 2)

    def _credit_manager_cashbox(self):
        """Post Приход entries to manager cashbox using confirmed_amount on submit."""
        cashbox = frappe.get_doc("Cashbox", self.to_cashbox)
        for line in self.lines or []:
            confirmed = flt(line.confirmed_amount)
            if confirmed <= 0:
                continue
            note = f"Инкассация от {self.from_cashbox} · {line.payment_method} [HAND:{self.name}]"
            if flt(line.discrepancy) != 0:
                note += f" · расхождение {line.discrepancy:+.2f}"
                if line.dispute_note:
                    note += f" ({line.dispute_note})"
            cashbox.append("transactions", {
                "transaction_type": "Приход",
                "payment_method": line.payment_method,
                "currency": line.currency or "USD",
                "exchange_rate": flt(getattr(line, "exchange_rate", 0)),
                "amount": confirmed,
                "category": "Перевод",
                "reference_doctype": "Cash Handover",
                "reference_name": self.name,
                "notes": note,
            })
        cashbox.save(ignore_permissions=True)

    def _debit_manager_cashbox(self):
        """Remove cashbox entries created on submit when handover is cancelled."""
        cashbox = frappe.get_doc("Cashbox", self.to_cashbox)
        before = len(cashbox.transactions or [])
        cashbox.transactions = [
            t for t in (cashbox.transactions or [])
            if not (
                t.reference_doctype == "Cash Handover"
                and t.reference_name == self.name
            )
        ]
        if len(cashbox.transactions) != before:
            cashbox.save(ignore_permissions=True)
