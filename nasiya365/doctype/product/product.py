"""
Product DocType Controller
Handles product attributes auto-population and BNPL settings validation
"""

import frappe
from frappe import _
from frappe.model.document import Document


class Product(Document):
    def onload(self):
        """Load category attributes when form loads"""
        if self.category and not self.attributes:
            self.load_category_attributes()
    
    def validate(self):
        self.validate_bnpl_settings()
        self.validate_required_attributes()
    
    def load_category_attributes(self):
        """Auto-populate attributes from category"""
        if not self.category:
            return
        
        category = frappe.get_doc("Product Category", self.category)
        if not category.attributes:
            return
        
        # Clear existing attributes
        self.attributes = []
        
        # Add attributes from category
        for cat_attr in category.attributes:
            self.append("attributes", {
                "attribute": cat_attr.attribute
            })
    
    def validate_required_attributes(self):
        """Validate that all required attributes have values"""
        if not self.category:
            return
        
        category = frappe.get_doc("Product Category", self.category)
        required_attrs = [attr.attribute for attr in category.attributes if attr.is_required]
        
        filled_attrs = [attr.attribute for attr in self.attributes if attr.value]
        
        for req_attr in required_attrs:
            if req_attr not in filled_attrs:
                frappe.throw(_("Attribute {0} is required for this category").format(req_attr))
    
    def validate_bnpl_settings(self):
        """Validate BNPL-related settings"""
        if self.allow_installment:
            if not self.min_down_payment_percent:
                self.min_down_payment_percent = 20  # Default 20%
            
            if self.min_down_payment_percent > 100:
                frappe.throw(_("Minimum down payment cannot exceed 100%"))
            
            if not self.max_installment_months:
                self.max_installment_months = 12  # Default 12 months


@frappe.whitelist()
def get_stock_balance(product, warehouse=None):
    """
    Get current stock balance for a product
    Returns: {'quantity': int, 'value': float}
    """
    filters = {"product": product}
    if warehouse:
        filters["warehouse"] = warehouse
    
    result = frappe.db.sql("""
        SELECT 
            COALESCE(SUM(balance_quantity), 0) as quantity,
            COALESCE(SUM(balance_quantity * valuation_rate), 0) as value
        FROM `tabStock Ledger`
        WHERE product = %s
        {warehouse_filter}
        GROUP BY product
    """.format(
        warehouse_filter="AND warehouse = %s" if warehouse else ""
    ), (product, warehouse) if warehouse else (product,), as_dict=True)
    
    if result:
        return result[0]
    return {"quantity": 0, "value": 0}


@frappe.whitelist()
def get_category_attributes(category):
    """Get attributes defined for a category"""
    if not category:
        return []
    
    category_doc = frappe.get_doc("Product Category", category)
    return [{"attribute": attr.attribute, "is_required": attr.is_required} 
            for attr in category_doc.attributes]
