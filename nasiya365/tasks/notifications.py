"""
Notification Tasks for Nasiya365
"""

import frappe
from frappe.utils import today
from nasiya365.utils.sms_manager import SMSManager


def send_due_today_reminders():
    """
    Send reminders for payments due today.
    Runs at 9 AM daily.
    """
    frappe.logger().info("Running: send_due_today_reminders")
    
    # Get installments due today
    due_today = frappe.db.sql("""
        SELECT 
            ip.customer,
            ip.name as installment_plan,
            isc.due_date,
            isc.amount,
            cp.full_name as customer_name
        FROM `tabInstallment Plan` ip
        INNER JOIN `tabInstallment Schedule` isc ON isc.parent = ip.name
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE isc.status IN ('Ожидает', 'Частично')
        AND isc.due_date = %s
    """, (today(),), as_dict=True)

    sms = SMSManager()
    for payment in due_today:
        phone = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": payment.customer, "is_primary": 1},
            "phone_number",
        )
        if not phone:
            continue
        message = f"Сегодня дата платежа: {payment.amount:,.0f} сум. Nasiya365"
        sms.send_sms(phone, message)
        frappe.logger().info(f"Due today reminder sent to {payment.customer_name}")
    
    frappe.logger().info(f"Sent {len(due_today)} due today reminders")


def send_overdue_warnings():
    """
    Send warnings for overdue payments.
    Runs at 6 PM daily.
    """
    frappe.logger().info("Running: send_overdue_warnings")
    
    # Get overdue installments
    overdue = frappe.db.sql("""
        SELECT 
            ip.customer,
            ip.name as installment_plan,
            isc.due_date,
            isc.amount,
            cp.full_name as customer_name,
            DATEDIFF(%s, isc.due_date) as days_overdue
        FROM `tabInstallment Plan` ip
        INNER JOIN `tabInstallment Schedule` isc ON isc.parent = ip.name
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE isc.status = 'Просрочен'
        ORDER BY days_overdue DESC
    """, (today(),), as_dict=True)

    sms = SMSManager()
    for payment in overdue:
        phone = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": payment.customer, "is_primary": 1},
            "phone_number",
        )
        if not phone:
            continue
        message = f"Просрочка {payment.days_overdue} дн. Сумма {payment.amount:,.0f} сум. Nasiya365"
        sms.send_sms(phone, message)
        frappe.logger().info(f"Overdue warning sent to {payment.customer_name} ({payment.days_overdue} days)")
    
    frappe.logger().info(f"Sent {len(overdue)} overdue warnings")
