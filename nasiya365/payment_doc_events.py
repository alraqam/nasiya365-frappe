"""
Document hooks for Payment Transaction (registered in hooks.py).
Keeps plan allocation on the hook path so it always runs after save.
"""

import frappe
from frappe.utils import cint, flt


def payment_transaction_after_insert(doc, method=None):
    from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
        allocate_payment_transaction_to_installment_plan,
    )

    allocate_payment_transaction_to_installment_plan(doc)


def payment_transaction_on_update(doc, method=None):
    if (doc.reference_doctype or "").strip() != "Installment Plan":
        return
    if not (doc.has_value_changed("reference_name") or doc.has_value_changed("reference_doctype")):
        return
    from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
        allocate_payment_transaction_to_installment_plan,
    )

    allocate_payment_transaction_to_installment_plan(doc)


def merchant_settings_on_update(doc, method=None):
    """When Merchant Settings is saved, push payment_methods into every
    payment_method Select field across all relevant DocTypes so the DB meta
    matches and all forms show the current list without a migration."""
    raw = (doc.payment_methods or "").strip()
    if not raw:
        return

    methods = [m.strip() for m in raw.splitlines() if m.strip()]
    if not methods:
        return

    base_opts = "\n".join(methods)

    # (doctype, fieldname, extra_options_appended)
    targets = [
        ("Payment Transaction Line", "payment_method", ""),
        ("Payment Transaction",      "payment_method", "\nКомбинированный"),
        ("Cashbox Transaction",      "payment_method", ""),
        ("Cash Handover Line",       "payment_method", "\nДругое"),
    ]

    for doctype, fieldname, suffix in targets:
        opts = base_opts + suffix
        frappe.db.sql(
            "UPDATE `tabDocField` SET options=%s WHERE parent=%s AND fieldname=%s",
            (opts, doctype, fieldname),
        )
        # Also update Custom Field if one exists
        frappe.db.sql(
            "UPDATE `tabCustom Field` SET options=%s WHERE dt=%s AND fieldname=%s",
            (opts, doctype, fieldname),
        )

    frappe.clear_cache(doctype="Payment Transaction Line")
    frappe.clear_cache(doctype="Payment Transaction")
    frappe.clear_cache(doctype="Cashbox Transaction")
    frappe.clear_cache(doctype="Cash Handover Line")


def installment_plan_refresh_stock_entry(doc, method=None):
    """After an installment plan is saved/cancelled, refresh the linked stock entry status."""
    stock_entry = getattr(doc, "stock_entry", None)
    if not stock_entry:
        return
    from nasiya365.nasiya365.doctype.stock_entry.stock_entry import refresh_stock_entry_business_status
    refresh_stock_entry_business_status(stock_entry)


def sales_order_refresh_stock_entries(doc, method=None):
    """After a sales order is submitted/cancelled, refresh stock entries whose serials appear in it."""
    imeis = [(i.imei or "").strip() for i in (doc.items or []) if (i.imei or "").strip()]
    if not imeis:
        return

    touched = set()
    for imei in imeis:
        norm = imei.upper().replace(" ", "")
        last6 = norm[-6:] if len(norm) >= 6 else norm
        rows = frappe.db.sql(
            """SELECT DISTINCT parent FROM `tabStock Entry Item`
               WHERE NULLIF(TRIM(imei), '') IS NOT NULL
                 AND (REPLACE(UPPER(TRIM(imei)),' ','') = %s
                      OR RIGHT(REPLACE(UPPER(TRIM(imei)),' ',''), 6) = %s)""",
            (norm, last6),
            pluck="parent",
        )
        touched.update(rows)

    from nasiya365.nasiya365.doctype.stock_entry.stock_entry import refresh_stock_entry_business_status
    for entry_name in touched:
        refresh_stock_entry_business_status(entry_name)


def product_before_validate(doc, method=None):
    """Coerce BNPL numeric fields to avoid str/int comparisons on Product validate."""
    if not getattr(doc, "allow_installment", None):
        return

    try:
        min_pct = flt(getattr(doc, "min_down_payment_percent", 0))
    except Exception:
        min_pct = 0
    if min_pct <= 0:
        min_pct = 20.0
    doc.min_down_payment_percent = min_pct

    try:
        max_m = cint(getattr(doc, "max_installment_months", 0))
    except Exception:
        max_m = 0
    if max_m <= 0:
        max_m = 12
    doc.max_installment_months = max_m
