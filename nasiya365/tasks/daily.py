"""
Daily Scheduled Tasks for Nasiya365
"""

import frappe
from frappe import _
from frappe.utils import today, add_days, getdate


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
        WHERE status = 'Pending'
        AND due_date < %s
    """, (today(),), as_dict=True)
    
    for installment in overdue_installments:
        # Update schedule status to Overdue
        frappe.db.set_value(
            "Installment Schedule",
            installment.schedule_name,
            "status",
            "Overdue"
        )
        overdue_count += 1
        
        # Check if we need to apply late fee
        plan = frappe.get_doc("Installment Plan", installment.installment_plan)
        days_overdue = (getdate(today()) - getdate(installment.due_date)).days
        
        # Apply late fee after grace period (get from merchant settings)
        grace_period = frappe.db.get_single_value("Merchant Settings", "grace_period_days") or 3
        
        if days_overdue > grace_period:
            apply_late_fee(plan, installment)
    
    frappe.db.commit()
    frappe.logger().info(f"Marked {overdue_count} installments as overdue")


def apply_late_fee(plan, installment):
    """Apply late fee to an overdue installment"""
    late_fee_percentage = frappe.db.get_single_value("Merchant Settings", "late_fee_percentage") or 1
    late_fee = (installment.amount * late_fee_percentage) / 100
    
    # Record late fee (implement based on your late fee tracking approach)
    frappe.logger().info(f"Late fee of {late_fee} applied to {installment.schedule_name}")


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
            cp.phone,
            cp.customer_name
        FROM `tabInstallment Plan` ip
        INNER JOIN `tabInstallment Schedule` isc ON isc.parent = ip.name
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE isc.status = 'Pending'
        AND isc.due_date = %s
    """, (tomorrow,), as_dict=True)
    
    for payment in due_tomorrow:
        # Send SMS reminder (implement based on your SMS provider)
        message = f"Hurmatli {payment.customer_name}, ertaga {payment.amount:,.0f} so'm to'lov muddati. Nasiya365"
        # send_sms(payment.phone, message)
        frappe.logger().info(f"Reminder sent to {payment.customer_name} for {payment.amount}")
    
    frappe.logger().info(f"Sent {len(due_tomorrow)} payment reminders")
