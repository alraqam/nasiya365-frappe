#!/usr/bin/env python3
"""
Cleanup script to delete imported legacy data before re-import
Run with: bench --site [site-name] execute nasiya365.cleanup_import.cleanup_legacy_imports
"""

import frappe

def cleanup_legacy_imports():
    """
    Delete all legacy imports (identified by po_no field)
    This will cascade delete related Installment Plans and Contracts
    """
    frappe.db.sql("""
        DELETE FROM `tabContract` 
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    print("✓ Deleted Contracts")
    
    frappe.db.sql("""
        DELETE FROM `tabInstallment Plan` 
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    print("✓ Deleted Installment Plans")
    
    frappe.db.sql("""
        DELETE FROM `tabStock Ledger`
        WHERE reference_doctype = 'Sales Order' 
        AND reference_name IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    print("✓ Deleted Stock Ledger entries")
    
    frappe.db.sql("""
        DELETE FROM `tabSales Order Item`
        WHERE parent IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL
        )
    """)
    print("✓ Deleted Sales Order Items")
    
    frappe.db.sql("""
        DELETE FROM `tabSales Order` 
        WHERE po_no IS NOT NULL
    """)
    print("✓ Deleted Sales Orders")
    
    frappe.db.commit()
    print("\n✅ All legacy import data has been cleaned up!")
    print("You can now run the import again with the fixed code.")

if __name__ == "__main__":
    cleanup_legacy_imports()
