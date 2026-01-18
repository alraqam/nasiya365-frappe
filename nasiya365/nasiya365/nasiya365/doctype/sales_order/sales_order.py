"""
Sales Order DocType Controller
Handles cash, BNPL, and mixed sales
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today


class SalesOrder(Document):
    def validate(self):
        self.set_defaults()
        self.calculate_totals()
        self.validate_sale_type()
    
    def before_insert(self):
        if not self.salesperson:
            self.salesperson = frappe.session.user
    
    def on_submit(self):
        self.update_stock()
        if self.sale_type in ["BNPL", "Mixed"]:
            self.create_installment_plan()
        if self.sale_type == "Cash":
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
        self.subtotal = sum(flt(item.amount) for item in self.items)
        
        # Calculate discount
        if self.discount_percent:
            self.discount_amount = (self.subtotal * self.discount_percent) / 100
        
        self.total_amount = self.subtotal - flt(self.discount_amount)
        self.balance_amount = self.total_amount - flt(self.paid_amount)
    
    def validate_sale_type(self):
        """Validate sale type specific requirements"""
        if self.sale_type == "Cash":
            if flt(self.paid_amount) < flt(self.total_amount):
                frappe.throw(_("For cash sale, paid amount must equal total amount"))
        
        elif self.sale_type == "BNPL":
            # Check customer eligibility
            customer = frappe.get_doc("Customer Profile", self.customer)
            if customer.status != "Active":
                frappe.throw(_("Customer is not eligible for BNPL"))
            
            if self.total_amount > customer.available_limit:
                frappe.throw(_("Order amount exceeds customer's available credit limit"))
            
            # Validate all items allow installment
            for item in self.items:
                product = frappe.get_doc("Product", item.product)
                if not product.allow_installment:
                    frappe.throw(
                        _("Product {0} does not allow installment purchase").format(
                            product.product_name
                        )
                    )
        
        elif self.sale_type == "Mixed":
            if flt(self.paid_amount) <= 0:
                frappe.throw(_("Mixed sale requires a partial cash payment"))
            if flt(self.paid_amount) >= flt(self.total_amount):
                frappe.throw(_("For full payment, use Cash sale type"))
    
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
            # Get from product purchase price
            valuation_rate = frappe.db.get_value("Product", product, "purchase_price") or 0
        
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
        plan.principal_amount = financed_amount
        plan.down_payment = self.paid_amount
        plan.interest_rate = settings.default_interest_rate or 0
        plan.number_of_installments = 6  # Default
        plan.frequency = "Monthly"
        plan.start_date = today()
        plan.insert()
        plan.submit()
        
        self.installment_plan = plan.name
        self.db_update()
    
    def create_cash_receipt(self):
        """Create payment transaction for full cash payment"""
        receipt = frappe.new_doc("Payment Transaction")
        receipt.customer = self.customer
        receipt.amount = self.paid_amount
        receipt.payment_method = "Cash"
        receipt.reference_doctype = "Sales Order"
        receipt.reference_name = self.name
        receipt.received_by = frappe.session.user
        receipt.insert()


def on_submit(doc, method):
    """Hook called when Sales Order is submitted"""
    doc.status = "Confirmed"
    doc.db_update()


def on_cancel(doc, method):
    """Hook called when Sales Order is cancelled"""
    doc.status = "Cancelled"
    doc.db_update()
