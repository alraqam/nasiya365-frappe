# cleanup_import.py - Add to nasiya365 module
import frappe

@frappe.whitelist()
def cleanup_legacy_imports():
    """
    Delete all legacy imports (identified by po_no field)
    This will cascade delete related Installment Plans and Contracts
    """
    frappe.only_for("System Manager")
    
    # Count before deletion
    count_so = frappe.db.count("Sales Order", {"po_no": ["is", "set"]})
    count_contract = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabContract` 
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)[0][0]
    count_plan = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabInstallment Plan` 
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)[0][0]
    
    # Delete in correct order
    frappe.db.sql("""
        DELETE FROM `tabContract` 
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    
    frappe.db.sql("""
        DELETE FROM `tabInstallment Plan` 
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)

    frappe.db.sql("""
        DELETE FROM `tabPayment Transaction` 
        WHERE reference_doctype = 'Sales Order' 
        AND reference_name IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    
    frappe.db.sql("""
        DELETE FROM `tabStock Ledger`
        WHERE reference_doctype = 'Sales Order' 
        AND reference_name IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    
    frappe.db.sql("""
        DELETE FROM `tabSales Order Item`
        WHERE parent IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    
    frappe.db.sql("""
        DELETE FROM `tabSales Order` 
        WHERE po_no IS NOT NULL
    """)
    
    frappe.db.commit()
    
    return {
        "deleted_sales_orders": count_so,
        "deleted_contracts": count_contract,
        "deleted_installment_plans": count_plan,
        "message": f"Deleted {count_so} Sales Orders, {count_contract} Contracts, and {count_plan} Installment Plans"
    }
