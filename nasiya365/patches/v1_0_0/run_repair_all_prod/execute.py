import frappe


def execute():
    """
    One-time production reconciliation patch.
    Non-blocking: logs and rolls back on failure, but does not break migrate.
    """
    try:
        from nasiya365.install import repair_all_prod

        repair_all_prod()
        frappe.db.commit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "run_repair_all_prod patch")
        frappe.db.rollback()

