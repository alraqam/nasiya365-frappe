import frappe


def execute():
    """Align existing Payment Transaction rows to the new submittable lifecycle.

    Until this patch, Payment Transaction had `is_submittable=0` and the code
    used `status` (Завершен / Отменен / Ожидает) as a pseudo-docstatus. We now
    flip `is_submittable=1`; align the existing rows:

        status='Завершен'              → docstatus=1
        status='Отменен'               → docstatus=2
        status='Ожидает' / NULL / ''   → docstatus=0 (no change)
    """
    frappe.db.sql(
        """
        UPDATE `tabPayment Transaction`
        SET docstatus = 1
        WHERE docstatus = 0
          AND status = 'Завершен'
        """
    )
    frappe.db.sql(
        """
        UPDATE `tabPayment Transaction`
        SET docstatus = 2
        WHERE status = 'Отменен'
        """
    )
    frappe.db.commit()
