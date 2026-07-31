import frappe


def execute():
    """Collapse Stock Entry.business_status to the simplified 4-value model:
    Черновик / В наличии / Продано / Возврат.

    The old model also had 'Частично продан', 'Наличные', 'Рассрочка', now removed:
      - 'Наличные' / 'Рассрочка' always meant a fully-sold receipt -> 'Продано'.
      - 'Частично продан' was reused for BOTH a partially-sold receipt (some units still
        available -> now 'В наличии') AND a fully-sold mixed-method receipt (a bug -> now
        'Продано'). The label alone can't distinguish them, so recompute each via
        refresh_stock_entry_business_status, which now returns only the new values.
    """
    from nasiya365.nasiya365.doctype.stock_entry.stock_entry import (
        refresh_stock_entry_business_status,
    )

    # Unambiguous: a receipt fully sold by one method becomes 'Продано'.
    frappe.db.sql(
        """UPDATE `tabStock Entry` SET business_status = 'Продано'
           WHERE business_status IN ('Наличные', 'Рассрочка')"""
    )

    # Ambiguous 'Частично продан': recompute per receipt -> splits into В наличии / Продано.
    names = frappe.db.sql_list(
        """SELECT name FROM `tabStock Entry`
           WHERE docstatus = 1 AND business_status = 'Частично продан'"""
    )
    for name in names:
        try:
            refresh_stock_entry_business_status(name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"migrate business_status: {name}")

    # Any leftover 'Частично продан' (serial-less receipts that refresh skips) -> still available.
    frappe.db.sql(
        """UPDATE `tabStock Entry` SET business_status = 'В наличии'
           WHERE business_status = 'Частично продан'"""
    )
    frappe.db.commit()
