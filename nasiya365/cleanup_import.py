# cleanup_import.py - Delete imported data by type
import frappe


@frappe.whitelist()
def cleanup_imported_data(import_type="all"):
    """
    Delete imported data by type or all at once.
    Imported records are identified by:
      - Sales Orders with po_no set (contracts/BNPL)
      - Stock Entries with remarks starting with 'Imported'
      - Customers/Suppliers created during import
    """
    frappe.only_for("System Manager")

    results = {}

    if import_type in ("all", "Импорт платежей"):
        results["payments"] = _delete_payments()

    if import_type in ("all", "Импорт договоров"):
        results["contracts"] = _delete_contracts()

    if import_type in ("all", "Импорт закупок"):
        results["purchases"] = _delete_purchases()

    if import_type in ("all", "Импорт складских записей"):
        results["stock_entries"] = _delete_stock_entries()

    # Products must be deleted after stock entries and contracts that reference them
    if import_type == "all":
        results["products"] = _delete_products()

    if import_type in ("all", "Импорт поставщиков"):
        results["suppliers"] = _delete_suppliers()

    if import_type in ("all", "Импорт клиентов"):
        results["customers"] = _delete_customers()

    frappe.db.commit()

    # Build summary
    parts = []
    for key, count in results.items():
        if count > 0:
            parts.append(f"{key}: {count}")

    msg = "Удаление завершено. " + (", ".join(parts) if parts else "Нечего удалять.")
    return {"message": msg, "details": results}


def _delete_payments():
    """Delete Payment Transactions linked to imported Sales Orders (po_no set)."""
    count = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabPayment Transaction`
        WHERE reference_doctype = 'Sales Order'
        AND reference_name IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
        )
    """)[0][0]

    if count:
        frappe.db.sql("""
            DELETE FROM `tabPayment Transaction`
            WHERE reference_doctype = 'Sales Order'
            AND reference_name IN (
                SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
            )
        """)

    return count


def _delete_contracts():
    """Delete Contracts, Installment Plans, Stock Ledger entries, and Sales Orders from imports."""

    # 1. Contracts
    count_contracts = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabContract`
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
        )
    """)[0][0]

    frappe.db.sql("""
        DELETE FROM `tabContract`
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
        )
    """)

    # 2. Installment Plans (including schedules)
    count_plans = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabInstallment Plan`
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
        )
    """)[0][0]

    frappe.db.sql("""
        DELETE FROM `tabInstallment Schedule`
        WHERE parent IN (
            SELECT name FROM `tabInstallment Plan`
            WHERE sales_order IN (
                SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
            )
        )
    """)

    frappe.db.sql("""
        DELETE FROM `tabInstallment Plan`
        WHERE sales_order IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
        )
    """)

    # 3. Stock Ledger entries linked to imported Sales Orders
    frappe.db.sql("""
        DELETE FROM `tabStock Ledger`
        WHERE reference_doctype = 'Sales Order'
        AND reference_name IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
        )
    """)

    # 4. Sales Order Items
    frappe.db.sql("""
        DELETE FROM `tabSales Order Item`
        WHERE parent IN (
            SELECT name FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
        )
    """)

    # 5. Sales Orders
    count_so = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
    """)[0][0]

    frappe.db.sql("""
        DELETE FROM `tabSales Order` WHERE po_no IS NOT NULL AND po_no != ''
    """)

    return count_contracts + count_plans + count_so


def _delete_purchases():
    """Delete Stock Entries created from purchase imports (remarks contain 'Imported Purchase')."""
    count = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabStock Entry`
        WHERE remarks LIKE 'Imported Purchase%%'
    """)[0][0]

    if count:
        # Stock Ledger entries linked to these Stock Entries
        frappe.db.sql("""
            DELETE FROM `tabStock Ledger`
            WHERE reference_doctype = 'Stock Entry'
            AND reference_name IN (
                SELECT name FROM `tabStock Entry` WHERE remarks LIKE 'Imported Purchase%%'
            )
        """)

        # Stock Entry Items
        frappe.db.sql("""
            DELETE FROM `tabStock Entry Item`
            WHERE parent IN (
                SELECT name FROM `tabStock Entry` WHERE remarks LIKE 'Imported Purchase%%'
            )
        """)

        # Stock Entries
        frappe.db.sql("""
            DELETE FROM `tabStock Entry` WHERE remarks LIKE 'Imported Purchase%%'
        """)

    return count


def _delete_stock_entries():
    """Delete Stock Entries created from stock import (remarks contain 'Imported Stock')."""
    count = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabStock Entry`
        WHERE remarks LIKE 'Imported Stock%%'
    """)[0][0]

    if count:
        frappe.db.sql("""
            DELETE FROM `tabStock Ledger`
            WHERE reference_doctype = 'Stock Entry'
            AND reference_name IN (
                SELECT name FROM `tabStock Entry` WHERE remarks LIKE 'Imported Stock%%'
            )
        """)

        frappe.db.sql("""
            DELETE FROM `tabStock Entry Item`
            WHERE parent IN (
                SELECT name FROM `tabStock Entry` WHERE remarks LIKE 'Imported Stock%%'
            )
        """)

        frappe.db.sql("""
            DELETE FROM `tabStock Entry` WHERE remarks LIKE 'Imported Stock%%'
        """)

    return count


def _delete_suppliers():
    """Delete all Suppliers (import creates them fresh)."""
    count = frappe.db.count("Supplier")
    if count:
        frappe.db.sql("DELETE FROM `tabSupplier`")
    return count


def _delete_customers():
    """Delete all Customer Profiles and their phone numbers."""
    count = frappe.db.count("Customer Profile")
    if count:
        frappe.db.sql("DELETE FROM `tabCustomer Phone Number`")
        frappe.db.sql("DELETE FROM `tabCustomer Profile`")
    return count


def _delete_products():
    """Delete all Products created during import."""
    count = frappe.db.count("Product")
    if count:
        # Delete attribute values first (child table)
        frappe.db.sql("DELETE FROM `tabProduct Attribute Value` WHERE parent IN (SELECT name FROM `tabProduct`)")
        frappe.db.sql("DELETE FROM `tabProduct`")
    return count


# Keep backward compatibility
@frappe.whitelist()
def cleanup_legacy_imports():
    """Legacy function — calls cleanup_imported_data('all')."""
    return cleanup_imported_data("all")
