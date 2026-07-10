import frappe


def execute():
    """Backfill Sales Order.status for already-submitted/cancelled orders.

    Sales Order.on_submit()/on_cancel() never set self.status -- that logic
    lived in orphaned module-level on_submit(doc, method)/on_cancel(doc, method)
    functions that hooks.py never wired up (doc_events only pointed Sales
    Order's on_submit/on_cancel at payment_doc_events.sales_order_refresh_stock_entries).
    So every already-submitted order is stuck showing status="Черновик",
    which reads as "still available for sale" even though docstatus=1. Drafts
    (docstatus=0) are left untouched.
    """
    frappe.db.sql(
        """UPDATE `tabSales Order`
           SET status = 'Подтвержден'
           WHERE docstatus = 1 AND IFNULL(status, '') != 'Подтвержден'"""
    )
    frappe.db.sql(
        """UPDATE `tabSales Order`
           SET status = 'Отменен'
           WHERE docstatus = 2 AND IFNULL(status, '') != 'Отменен'"""
    )
    frappe.db.commit()
