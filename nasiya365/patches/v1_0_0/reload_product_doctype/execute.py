import frappe


def execute():
    """Reload Product DocType from JSON so selling_price and cost_price
    are present in the DB meta (required for fetch_from in Sales Order Item)."""
    frappe.reload_doc("nasiya365", "doctype", "product", force=True)
    frappe.reload_doc("nasiya365", "doctype", "sales_order_item", force=True)
