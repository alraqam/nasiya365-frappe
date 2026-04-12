"""
Daily Scheduled Tasks for Nasiya365
"""

import frappe
from frappe.utils import today, add_days
from nasiya365.utils.sms_manager import SMSManager


def check_overdue_installments():
    """
    Bulk-mark past-due schedule rows as Просрочен and refresh overdue_installments
    counter on affected plans. Skips closed/written-off plans.
    Runs daily at midnight.
    """
    frappe.logger().info("Running: check_overdue_installments")

    # Single bulk UPDATE — only rows on active plans
    result = frappe.db.sql(
        """
        UPDATE `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        SET isc.status = 'Просрочен',
            isc.modified = NOW()
        WHERE isc.status IN ('Ожидает', 'Частично', 'Pending')
          AND isc.due_date < %(today)s
          AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан')
        """,
        {"today": today()},
    )
    overdue_count = frappe.db.sql("SELECT ROW_COUNT()")[0][0]

    # Refresh overdue_installments counter on every plan that has at least one overdue row
    affected_plans = frappe.db.sql(
        """
        SELECT DISTINCT isc.parent
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE isc.status = 'Просрочен'
          AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан')
        """,
        pluck=True,
    ) or []

    for plan_name in affected_plans:
        overdue = frappe.db.count(
            "Installment Schedule",
            {"parent": plan_name, "status": "Просрочен"},
        )
        frappe.db.set_value(
            "Installment Plan", plan_name, "overdue_installments", overdue,
            update_modified=False,
        )

    frappe.db.commit()
    frappe.logger().info(
        f"Marked {overdue_count} installment(s) as overdue across {len(affected_plans)} plan(s)"
    )


def send_payment_reminders():
    """
    Send payment reminders for installments due tomorrow.
    Runs daily.
    """
    frappe.logger().info("Running: send_payment_reminders")
    
    tomorrow = add_days(today(), 1)
    
    # Get installments due tomorrow
    due_tomorrow = frappe.db.sql("""
        SELECT 
            ip.customer,
            ip.name as installment_plan,
            isc.due_date,
            isc.amount,
            cp.full_name as customer_name
        FROM `tabInstallment Plan` ip
        INNER JOIN `tabInstallment Schedule` isc ON isc.parent = ip.name
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE isc.status IN ('Ожидает', 'Частично', 'Pending')
        AND isc.due_date = %s
    """, (tomorrow,), as_dict=True)

    sms = SMSManager()
    for payment in due_tomorrow:
        phone = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": payment.customer, "is_primary": 1},
            "phone_number",
        )
        if not phone:
            continue
        message = f"Напоминание: завтра платеж {payment.amount:,.0f} сум. Nasiya365"
        sms.send_sms(phone, message)
        frappe.logger().info(f"Reminder sent to {payment.customer_name} for {payment.amount}")
    
    frappe.logger().info(f"Sent {len(due_tomorrow)} payment reminders")
