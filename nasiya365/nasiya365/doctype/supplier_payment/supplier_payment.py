"""
Supplier Payment DocType Controller

    docstatus=0, status=Ожидает    → draft, not yet posted to purchases/cashbox
    docstatus=1, status=Завершен   → posted: outstanding Stock Entries paid down
                                      (oldest first), cashbox Расход row added
    docstatus=2, status=Отменен    → cancelled: allocation + cashbox row reversed

Allocation is recorded in a dedicated `allocations` child table (Supplier
Payment Allocation) rather than a single link field on Stock Entry, so that
cancelling one payment can reverse exactly the amounts *this* payment applied
-- never by re-deriving "whichever payment currently references this row",
which is what let one payment's cancellation wipe another payment's
contribution in Payment Transaction / Installment Schedule.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


def _apply_payment_totals(doc):
    """Calculate amount + payment_method from payment_lines rows (mirrors
    Payment Transaction's _apply_table_payment_totals)."""
    if not doc.payment_lines:
        frappe.throw(_("Добавьте хотя бы одну строку в детали оплаты"))

    total = 0.0
    methods = set()
    default_rate = flt(doc.exchange_rate)

    for row in doc.payment_lines:
        row_amount = flt(row.amount)
        if row_amount <= 0:
            continue

        row_currency = (row.currency or "USD").strip().upper()
        if row_currency == "UZS":
            rate = flt(row.exchange_rate or default_rate)
            if rate <= 0:
                frappe.throw(
                    _("Укажите корректный курс USD для строки {0} (UZS)").format(row.idx)
                )
            total += row_amount / rate
        else:
            total += row_amount

        if row.payment_method:
            methods.add(row.payment_method.strip())

    if total <= 0:
        frappe.throw(_("Сумма по строкам оплаты должна быть больше нуля"))

    doc.amount = total
    if len(methods) > 1:
        doc.payment_method = "Комбинированный"
    elif len(methods) == 1:
        doc.payment_method = list(methods)[0]


def _outstanding_stock_entries_for_update(supplier, branch):
    """Lock and return this supplier+branch's outstanding Поступление entries,
    oldest first. FOR UPDATE serializes concurrent Supplier Payments against
    the same supplier so two payments can't both allocate against the same
    balance_due."""
    return frappe.db.sql(
        """
        SELECT se.name, se.posting_date, se.paid_amount, se.balance_due
        FROM `tabStock Entry` se
        INNER JOIN `tabWarehouse` w ON w.name = se.warehouse
        WHERE se.docstatus = 1
          AND se.entry_type = 'Поступление'
          AND se.supplier = %s
          AND w.branch = %s
          AND IFNULL(se.balance_due, 0) > 0.001
        ORDER BY se.posting_date ASC, se.creation ASC
        FOR UPDATE
        """,
        (supplier, branch),
        as_dict=True,
    )


def allocate_supplier_payment(doc):
    """Apply this payment to the supplier's outstanding purchases in this
    branch, oldest first. Idempotent: re-running after allocation rows already
    exist is a no-op."""
    if getattr(doc, "_nasiya_supplier_allocated", False):
        return
    if doc.name and frappe.db.exists(
        "Supplier Payment Allocation", {"parent": doc.name, "parenttype": "Supplier Payment"}
    ):
        doc._nasiya_supplier_allocated = True
        return

    remaining = flt(doc.amount)
    if remaining <= 0:
        return

    entries = _outstanding_stock_entries_for_update(doc.supplier, doc.branch)
    idx = 0
    for se in entries:
        if remaining <= 0.001:
            break
        take = min(remaining, flt(se.balance_due))
        if take <= 0:
            continue

        new_paid = flt(se.paid_amount) + take
        new_balance = flt(se.balance_due) - take
        frappe.db.set_value(
            "Stock Entry",
            se.name,
            {
                "paid_amount": new_paid,
                "balance_due": new_balance,
                "payment_status": "Оплачено" if new_balance <= 0.001 else "Частично оплачено",
            },
            update_modified=False,
        )

        idx += 1
        child = frappe.get_doc({
            "doctype": "Supplier Payment Allocation",
            "parent": doc.name,
            "parenttype": "Supplier Payment",
            "parentfield": "allocations",
            "idx": idx,
            "stock_entry": se.name,
            "posting_date": se.posting_date,
            "amount": take,
        })
        child.insert(ignore_permissions=True)
        doc.append("allocations", child)

        remaining -= take

    if remaining > 0.001:
        frappe.msgprint(
            _("Сумма превышает общий долг поставщику на {0} USD. Излишек не распределён.").format(
                f"{remaining:.2f}"
            ),
            indicator="orange",
            alert=True,
        )
        frappe.log_error(
            f"Supplier Payment {doc.name}: excess of {remaining:.4f} USD over outstanding "
            f"balance for supplier {doc.supplier} in branch {doc.branch}",
            "Supplier Payment: Overpayment",
        )

    doc._nasiya_supplier_allocated = True


def _deallocate_supplier_payment(doc):
    """Reverse using this payment's own recorded allocation rows -- never by
    re-deriving which Stock Entry currently "belongs" to it."""
    if not getattr(doc, "name", None):
        return
    rows = frappe.get_all(
        "Supplier Payment Allocation",
        filters={"parent": doc.name, "parenttype": "Supplier Payment"},
        fields=["stock_entry", "amount"],
    )
    for row in rows:
        se = frappe.db.get_value(
            "Stock Entry", row.stock_entry, ["paid_amount", "total_value"], as_dict=True
        )
        if not se:
            continue
        new_paid = flt(se.paid_amount) - flt(row.amount)
        new_balance = flt(se.total_value) - new_paid
        frappe.db.set_value(
            "Stock Entry",
            row.stock_entry,
            {
                "paid_amount": new_paid,
                "balance_due": new_balance,
                "payment_status": (
                    "Не оплачено" if new_paid <= 0.001
                    else "Оплачено" if new_balance <= 0.001
                    else "Частично оплачено"
                ),
            },
            update_modified=False,
        )


def _get_cashbox_for_payment(doc):
    from nasiya365.nasiya365.doctype.cashbox.cashbox import _find_master_cashbox

    return _find_master_cashbox(doc.branch)


def _sync_supplier_payment_to_cashbox(doc):
    """Post one Расход row per payment_lines row (idempotent, concurrent-safe;
    mirrors Payment Transaction's _sync_payment_to_cashbox)."""
    if getattr(doc, "_nasiya_cashbox_synced", False):
        return
    if (doc.status or "").strip() != "Завершен":
        return
    if not doc.payment_lines:
        return

    cashbox_name = _get_cashbox_for_payment(doc)
    if not cashbox_name:
        frappe.log_error(
            "Open cashbox not found", f"Supplier Payment cashbox sync: {doc.name}"
        )
        frappe.throw(
            _("Нет открытой кассы для филиала {0}. Откройте кассу перед оплатой поставщику.").format(
                doc.branch
            )
        )

    already_synced = frappe.db.sql(
        """SELECT 1 FROM `tabCashbox Transaction`
           WHERE parent = %s
             AND reference_doctype = 'Supplier Payment'
             AND reference_name = %s
           LIMIT 1""",
        (cashbox_name, doc.name),
    )
    if already_synced:
        doc._nasiya_cashbox_synced = True
        return

    from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
        _normalize_payment_line_method,
    )

    rows_to_add = []
    for row in doc.payment_lines:
        line_amount = flt(row.amount)
        if line_amount <= 0:
            continue
        line_method = _normalize_payment_line_method(row.payment_method)
        line_currency = (row.currency or "USD").strip().upper()
        line_rate = flt(getattr(row, "exchange_rate", 0))
        marker = f"[SPAY:{doc.name}|ROW:{row.idx}]"
        rows_to_add.append({
            "transaction_type": "Расход",
            "payment_method": line_method,
            "currency": line_currency,
            "exchange_rate": line_rate,
            "amount": line_amount,
            "category": "Оплата поставщику",
            "reference_doctype": "Supplier Payment",
            "reference_name": doc.name,
            "notes": f"Оплата поставщику {doc.supplier} · {line_method} · {line_currency} {marker}",
        })

    if not rows_to_add:
        doc._nasiya_cashbox_synced = True
        return

    cashbox = frappe.get_doc("Cashbox", cashbox_name)
    already_in_reload = any(
        t.reference_doctype == "Supplier Payment" and t.reference_name == doc.name
        for t in (cashbox.transactions or [])
    )
    if already_in_reload:
        doc._nasiya_cashbox_synced = True
        return

    for row_data in rows_to_add:
        cashbox.append("transactions", row_data)
    cashbox.save(ignore_permissions=True)
    doc._nasiya_cashbox_synced = True


def _remove_supplier_payment_from_cashbox(doc):
    """Remove all cashbox rows created for this payment (mirrors
    Payment Transaction's _remove_payment_from_cashbox)."""
    if not getattr(doc, "name", None):
        return
    cashboxes = frappe.get_all(
        "Cashbox",
        filters={"status": ["in", ["Открыта", "Закрыта"]]},
        pluck="name",
    )
    if not cashboxes:
        return
    for cashbox_name in cashboxes:
        cashbox = frappe.get_doc("Cashbox", cashbox_name)
        before = len(cashbox.transactions or [])
        cashbox.transactions = [
            t
            for t in (cashbox.transactions or [])
            if not (
                (t.reference_doctype == "Supplier Payment") and (t.reference_name == doc.name)
            )
        ]
        if len(cashbox.transactions or []) != before:
            cashbox.save(ignore_permissions=True)


class SupplierPayment(Document):
    def validate(self):
        _apply_payment_totals(self)

    def before_insert(self):
        if not self.paid_by:
            self.paid_by = frappe.session.user

    def on_submit(self):
        self.db_set("status", "Завершен", update_modified=False)
        frappe.db.savepoint("nasiya_supplier_payment_on_submit")
        try:
            allocate_supplier_payment(self)
            _sync_supplier_payment_to_cashbox(self)
        except Exception:
            frappe.db.rollback(save_point="nasiya_supplier_payment_on_submit")
            raise

    def on_cancel(self):
        self.db_set("status", "Отменен", update_modified=False)
        _deallocate_supplier_payment(self)
        _remove_supplier_payment_from_cashbox(self)
        self.add_comment(
            "Info",
            _("Платёж поставщику отменён пользователем {0}. Сумма: {1} USD.").format(
                frappe.session.user, flt(self.amount, 2)
            ),
        )

    def on_trash(self):
        from nasiya365.permissions import admin_only_for_submitted_delete

        admin_only_for_submitted_delete(self)
