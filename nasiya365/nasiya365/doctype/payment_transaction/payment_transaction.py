"""
Payment Transaction DocType Controller
"""

import frappe
from frappe.model.document import Document


class PaymentTransaction(Document):
    def before_insert(self):
        if not self.received_by:
            self.received_by = frappe.session.user
            
    def after_insert(self):
        """
        Logic to run after a payment is inserted.
        This is referenced in hooks.py.
        """
        if self.reference_doctype == "Installment Plan" and self.reference_name:
            plan = frappe.get_doc("Installment Plan", self.reference_name)
            plan.apply_payment(self.amount, payment_transaction=self.name)


@frappe.whitelist()
def get_customer_installment_plans(customer):
    """Return all installment plans for a customer with debt + device info."""
    if not customer:
        return []

    rows = frappe.db.sql(
        """
        SELECT
            ip.name,
            ip.status,
            ip.contract_status,
            ip.remaining_balance,
            ip.total_amount,
            ip.installment_amount,
            ip.sales_order,
            ip.stock_entry,
            ip.imei,
            ip.contract_number,
            COALESCE(NULLIF(TRIM(CONCAT_WS(' · ',
                NULLIF(TRIM(COALESCE(
                    NULLIF(ip.product_name, ''),
                    NULLIF(p.product_name, ''),
                    NULLIF(soi.product_name, ''),
                    ''
                )), ''),
                NULLIF(TRIM(COALESCE(NULLIF(sei.color, ''), NULLIF(soi.color, ''), '')), ''),
                NULLIF(TRIM(COALESCE(NULLIF(sei.storage, ''), NULLIF(soi.storage, ''), '')), '')
            )), ''), '') AS device_name
        FROM `tabInstallment Plan` ip
        LEFT JOIN `tabStock Entry Item` sei
            ON sei.parent = ip.stock_entry AND sei.idx = 1
        LEFT JOIN `tabProduct` p
            ON p.name = sei.product
        LEFT JOIN `tabSales Order Item` soi
            ON soi.parent = ip.sales_order AND soi.idx = 1
        WHERE ip.customer = %s
        ORDER BY ip.modified DESC
        """,
        (customer,),
        as_dict=True,
    )
    return rows or []
