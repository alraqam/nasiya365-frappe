"""
Daily Scheduled Tasks for Nasiya365
"""

import frappe
from frappe.utils import today, add_days
from nasiya365.utils.sms_manager import SMSManager


def check_overdue_installments():
    """
    Check for overdue installments and update their status.
    Runs daily at midnight.
    """
    frappe.logger().info("Running: check_overdue_installments")
    
    overdue_count = 0
    
    # Get all pending installments that are past due
    overdue_installments = frappe.db.sql("""
        SELECT 
            parent as installment_plan,
            name as schedule_name,
            due_date,
            amount
        FROM `tabInstallment Schedule`
        WHERE status IN ('Ожидает', 'Частично', 'Pending')
        AND due_date < %s
    """, (today(),), as_dict=True)
    
    for installment in overdue_installments:
        # Update schedule status to Overdue
        frappe.db.set_value(
            "Installment Schedule",
            installment.schedule_name,
            "status",
            "Просрочен"
        )
        overdue_count += 1
    
    frappe.db.commit()
    frappe.logger().info(f"Marked {overdue_count} installments as overdue")


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
