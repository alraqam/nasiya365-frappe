"""
Weekly Scheduled Tasks for Nasiya365
"""

import frappe
from frappe.utils import today, add_days, getdate


def generate_collection_report():
    """
    Generate weekly collection report.
    Summarizes payments received, overdue amounts, and collection efficiency.
    """
    frappe.logger().info("Running: generate_collection_report")
    
    # Calculate date range (last 7 days)
    end_date = today()
    start_date = add_days(end_date, -7)
    
    # Get total collected amount
    collected = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) as total
        FROM `tabPayment Transaction`
        WHERE status = 'Completed'
        AND payment_date BETWEEN %s AND %s
    """, (start_date, end_date))[0][0]
    
    # Get total overdue amount
    overdue = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) as total
        FROM `tabInstallment Schedule`
        WHERE status = 'Overdue'
    """)[0][0]
    
    # Get total expected this week
    expected = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) as total
        FROM `tabInstallment Schedule`
        WHERE due_date BETWEEN %s AND %s
    """, (start_date, end_date))[0][0]
    
    # Calculate collection efficiency
    efficiency = (collected / expected * 100) if expected > 0 else 0
    
    report_data = {
        "period": f"{start_date} to {end_date}",
        "collected": collected,
        "expected": expected,
        "overdue": overdue,
        "efficiency": f"{efficiency:.1f}%"
    }
    
    frappe.logger().info(f"Weekly Report: {report_data}")
    
    # TODO: Save report or send to managers
    return report_data
