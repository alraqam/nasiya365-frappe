"""
Sales Order DocType Controller
Handles cash, BNPL, and mixed sales
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

from nasiya365.utils.credit import is_customer_credit_limit_enforced


class SalesOrder(Document):
    def validate(self):
        self.set_defaults()
        self.calculate_totals()
        self.validate_payment_rules()
        self.validate_stock_available()
        self.set_order_kind()
    
    def before_insert(self):
        if not self.salesperson:
            self.salesperson = frappe.session.user
    
    def on_submit(self):
        self.update_stock()
        if flt(self.balance_amount) > 0:
            self.create_installment_plan()
        # Skip creating payment transaction during import (legacy data)
        if flt(self.balance_amount) <= 0 and not frappe.flags.in_import:
            self.create_cash_receipt()
    
    def on_cancel(self):
        self.reverse_stock()
    
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
        if self.discount_percent:
            self.discount_amount = (self.subtotal * self.discount_percent) / 100
        
        self.total_amount = self.subtotal - flt(self.discount_amount)
        self.balance_amount = self.total_amount - flt(self.paid_amount)
    
    def validate_payment_rules(self):
        """No manual sale type: derive mode from paid/balance."""
        if flt(self.paid_amount) < 0:
            frappe.throw(_("Оплачено не может быть отрицательным"))
        if flt(self.paid_amount) > flt(self.total_amount):
            frappe.throw(_("Оплачено не может превышать общую сумму"))

        # BNPL eligibility when there is outstanding balance.
        if flt(self.balance_amount) > 0:
            customer = frappe.get_doc("Customer Profile", self.customer)
            if customer.status != "Активный":
                frappe.throw(_("Клиент не имеет права на рассрочку (BNPL)"))
            if is_customer_credit_limit_enforced() and flt(customer.credit_limit) > 0:
                customer.update_statistics()
                if self.total_amount > flt(customer.available_limit):
                    frappe.throw(_("Сумма заказа превышает доступный кредитный лимит клиента"))
            for item in self.items:
                product = frappe.get_doc("Product", item.product)
                if not product.allow_installment:
                    frappe.throw(
                        _("Товар {0} недоступен для покупки в рассрочку").format(
                            product.product_name
                        )
                    )

    def set_order_kind(self):
        self.order_kind = "Rassrochka" if flt(self.balance_amount) > 0 else "Naqd Savdo"

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
    
    def create_installment_plan(self):
        """Create installment plan for BNPL/Mixed sales"""
        if self.installment_plan:
            return  # Already created
        
        # Amount to finance
        financed_amount = self.total_amount - flt(self.paid_amount)
        
        # Get default settings
        settings = frappe.get_single("Merchant Settings")
        
        plan = frappe.new_doc("Installment Plan")
        plan.customer = self.customer
        plan.sales_order = self.name
        plan.principal_amount = self.total_amount
        plan.down_payment = self.paid_amount
        plan.interest_rate = settings.default_interest_rate or 0
        plan.number_of_installments = 6  # Default
        plan.frequency = "Ежемесячно (Monthly)"
        plan.status = "Активный"
        plan.start_date = today()
        plan.insert()
        
        self.installment_plan = plan.name
        self.db_update()
    
    def create_cash_receipt(self):
        """Create payment transaction for full cash payment"""
        receipt = frappe.new_doc("Payment Transaction")
        receipt.customer = self.customer
        receipt.payment_method = "Наличные"
        receipt.reference_doctype = "Sales Order"
        receipt.reference_name = self.name
        receipt.received_by = frappe.session.user
        receipt.append(
            "payment_lines",
            {
                "payment_method": "Наличные",
                "currency": "USD",
                "amount": flt(self.paid_amount),
            },
        )
        receipt.insert()


def on_submit(doc, method):
    """Hook called when Sales Order is submitted"""
    doc.status = "Подтвержден"
    doc.order_kind = "Rassrochka" if flt(doc.balance_amount) > 0 else "Naqd Savdo"
    doc.db_update()


def on_cancel(doc, method):
    """Hook called when Sales Order is cancelled"""
    doc.status = "Отменен"
    doc.db_update()


@frappe.whitelist()
def get_product_stock_available(product, warehouse=None):
    if not product:
        return {"available_qty": 0}
    if not warehouse:
        warehouse = frappe.db.get_value("Warehouse", {"is_group": 0}, "name")
    qty = frappe.db.sql(
        """
        SELECT COALESCE(SUM(quantity_change), 0)
        FROM `tabStock Ledger`
        WHERE product = %s AND warehouse = %s
        """,
        (product, warehouse),
    )[0][0]
    return {"available_qty": flt(qty), "warehouse": warehouse}
