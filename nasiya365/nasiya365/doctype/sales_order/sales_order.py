"""
Sales Order DocType Controller
Handles cash sales (no installment/BNPL link)
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today


class SalesOrder(Document):
    def validate(self):
        self.set_defaults()
        self.calculate_totals()
        self.validate_payment_rules()
        self.validate_stock_available()

    def before_insert(self):
        if not self.salesperson:
            self.salesperson = frappe.session.user

    def before_submit(self):
        # Legacy BNPL records carry an outstanding balance; skip the cash-only
        # check during data import so historical orders still submit.
        if frappe.flags.in_import:
            return
        if flt(self.balance_amount) > 0:
            frappe.throw(_("Заказ должен быть полностью оплачен перед проведением (продажа только за наличные)"))

    def on_submit(self):
        self.update_stock()
        # Skip creating payment transaction during import (legacy data)
        if not frappe.flags.in_import:
            self.create_cash_receipt()
    
    def on_cancel(self):
        self.reverse_stock()

    def on_trash(self):
        """Block deletion if any Installment Plan references this SO — they need
        to be cancelled first so the cascade reverses payments / contracts cleanly."""
        from nasiya365.permissions import admin_only_for_submitted_delete

        admin_only_for_submitted_delete(self)
        ip_count = frappe.db.count("Installment Plan", {"sales_order": self.name})
        if ip_count:
            frappe.throw(
                _(
                    "Невозможно удалить заказ: к нему привязан план рассрочки. "
                    "Сначала отмените план."
                )
            )
    
    def set_defaults(self):
        """Set default values"""
        if not self.warehouse and self.branch:
            # Get default warehouse for branch
            default_wh = frappe.db.get_value(
                "Warehouse",
                {"branch": self.branch, "is_default": 1},
                "name"
            )
            if default_wh:
                self.warehouse = default_wh
    
    def calculate_totals(self):
        """Calculate subtotal, discount, and total amounts"""
        for item in self.items:
            item.amount = flt(item.quantity) * flt(item.unit_price) * (1 - flt(item.discount_percent) / 100.0)
            
        self.subtotal = sum(flt(item.amount) for item in self.items)

        # Calculate discount
        if flt(self.discount_percent):
            self.discount_amount = (self.subtotal * flt(self.discount_percent)) / 100

        self.total_amount = self.subtotal - flt(self.discount_amount)
        self.balance_amount = self.total_amount - flt(self.paid_amount)
    
    def validate_payment_rules(self):
        if flt(self.paid_amount) < 0:
            frappe.throw(_("Оплачено не может быть отрицательным"))

    def validate_stock_available(self):
        """Ensure requested qty does not exceed on-hand stock."""
        for item in self.items:
            if not item.product or not self.warehouse:
                continue
            balance = frappe.db.sql(
                """
                SELECT COALESCE(SUM(quantity_change), 0)
                FROM `tabStock Ledger`
                WHERE product = %s AND warehouse = %s
                """,
                (item.product, self.warehouse),
            )[0][0]
            if flt(item.quantity) > flt(balance):
                pname = item.product_name or item.product
                frappe.throw(
                    _("Недостаточно остатка для {0}. Доступно: {1}, требуется: {2}").format(
                        pname, flt(balance), flt(item.quantity)
                    )
                )
    
    def update_stock(self):
        """Reduce stock for sold items"""
        for item in self.items:
            self.create_stock_ledger_entry(
                product=item.product,
                warehouse=self.warehouse,
                quantity=-item.quantity,  # Negative for reduction
                reference=self.name
            )
    
    def reverse_stock(self):
        """Restore stock when order is cancelled"""
        for item in self.items:
            self.create_stock_ledger_entry(
                product=item.product,
                warehouse=self.warehouse,
                quantity=item.quantity,  # Positive for restoration
                reference=self.name
            )
    
    def create_stock_ledger_entry(self, product, warehouse, quantity, reference):
        """Create stock ledger entry"""
        # Get current balance
        current = frappe.db.sql("""
            SELECT COALESCE(balance_quantity, 0), COALESCE(valuation_rate, 0)
            FROM `tabStock Ledger`
            WHERE product = %s AND warehouse = %s
            ORDER BY posting_date DESC, creation DESC
            LIMIT 1
        """, (product, warehouse))
        
        current_qty = current[0][0] if current else 0
        valuation_rate = current[0][1] if current else 0
        
        if not valuation_rate:
            valuation_rate = 0
        
        new_balance = current_qty + quantity
        
        ledger = frappe.new_doc("Stock Ledger")
        ledger.product = product
        ledger.warehouse = warehouse
        ledger.quantity_change = quantity
        ledger.balance_quantity = new_balance
        ledger.valuation_rate = valuation_rate
        ledger.reference_doctype = "Sales Order"
        ledger.reference_name = reference
        ledger.posting_date = today()
        ledger.insert(ignore_permissions=True)
    
    def create_cash_receipt(self):
        """Create payment transaction for full cash payment"""
        receipt = frappe.new_doc("Payment Transaction")
        receipt.customer = self.customer
        receipt.payment_method = "Наличные USD"
        receipt.reference_doctype = "Sales Order"
        receipt.reference_name = self.name
        receipt.received_by = frappe.session.user
        receipt.append(
            "payment_lines",
            {
                "payment_method": "Наличные USD",
                "currency": "USD",
                "amount": flt(self.paid_amount),
            },
        )
        receipt.insert()
        # Submit so on_submit fires (status=Завершен, allocate, cashbox sync).
        receipt.submit()


def on_submit(doc, method):
    """Hook called when Sales Order is submitted"""
    doc.status = "Подтвержден"
    doc.db_update()


def on_cancel(doc, method):
    """Hook called when Sales Order is cancelled"""
    doc.status = "Отменен"
    doc.db_update()


@frappe.whitelist()
def get_product_for_wizard(product):
    """Return product fields for the picked stock entry item, bypassing get_value field validation."""
    if not product:
        return {}
    rows = frappe.db.sql(
        """SELECT selling_price, cost_price, product_name, allow_installment,
                  min_down_payment_percent, max_installment_months,
                  color, storage, `condition`
           FROM `tabProduct` WHERE name = %s LIMIT 1""",
        product,
        as_dict=True,
    )
    if not rows:
        return {}
    row = rows[0]
    return {
        "selling_price": flt(row.get("selling_price") or 0),
        "cost_price": flt(row.get("cost_price") or 0),
        "product_name": row.get("product_name") or product,
        "allow_installment": row.get("allow_installment"),
        "min_down_payment_percent": flt(row.get("min_down_payment_percent") or 0),
        "max_installment_months": row.get("max_installment_months"),
        "color": row.get("color") or "",
        "storage": row.get("storage") or "",
        "condition": row.get("condition") or "",
    }


@frappe.whitelist()
def get_product_stock_available(product, warehouse=None):
    if not product:
        return {"available_qty": 0}
    if not warehouse:
        warehouse = frappe.db.get_value("Warehouse", {"is_default": 1}, "name")
        if not warehouse:
            warehouse = frappe.db.get_value("Warehouse", {}, "name")
    qty = frappe.db.sql(
        """
        SELECT COALESCE(SUM(quantity_change), 0)
        FROM `tabStock Ledger`
        WHERE product = %s AND warehouse = %s
        """,
        (product, warehouse),
    )[0][0]
    return {"available_qty": flt(qty), "warehouse": warehouse}
