import frappe


def execute():
    """Reload Payment Transaction Line DocType from JSON to remove bare 'Наличные'
    from payment_method options and set 'Наличные USD' as the default."""
    frappe.reload_doc("nasiya365", "doctype", "payment_transaction_line", force=True)
    frappe.clear_cache(doctype="Payment Transaction Line")
    frappe.clear_cache(doctype="Payment Transaction")
