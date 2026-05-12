import frappe


def execute():
    """Migrate existing Installment Plan rows to the new submittable lifecycle.

    Background: until this patch landed the DocType was `is_submittable=0`,
    yet code paths (data_import.py, sales_order.create_installment_plan,
    payment_transaction revert) set `status='Активный'` while leaving
    `docstatus=0`. We now flip `is_submittable=1`; align the existing rows
    so docstatus reflects the lifecycle:

        status='Активный' | 'Просрочен' | 'Завершен'  → docstatus=1
        status='Отменен'                                → docstatus=2
        status='Черновик' or NULL                       → docstatus=0 (no change)
    """
    # status ∈ {Активный, Просрочен, Завершен} → submitted
    frappe.db.sql(
        """
        UPDATE `tabInstallment Plan`
        SET docstatus = 1
        WHERE docstatus = 0
          AND status IN ('Активный', 'Просрочен', 'Завершен')
        """
    )
    # status='Отменен' → cancelled
    frappe.db.sql(
        """
        UPDATE `tabInstallment Plan`
        SET docstatus = 2
        WHERE status = 'Отменен'
        """
    )
    frappe.db.commit()
