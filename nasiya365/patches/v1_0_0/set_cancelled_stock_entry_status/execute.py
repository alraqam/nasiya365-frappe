import frappe


def execute():
    """Mark every cancelled Stock Entry (docstatus=2) as «Отменён».

    Cancelling a receipt used to leave its pre-cancel status untouched (a submitted receipt
    kept «В наличии»), or set «Возврат» only when it had been empty/«Черновик» — so a voided
    receipt wrongly read as available stock. on_cancel now always sets «Отменён»; this
    backfills existing cancelled rows. Submitted receipts (docstatus=1) are left alone, so a
    deliberate «Возврат» on an active receipt (an actual return of goods) survives.
    """
    frappe.db.sql(
        "UPDATE `tabStock Entry` SET business_status = 'Отменён' WHERE docstatus = 2"
    )
    frappe.db.commit()
