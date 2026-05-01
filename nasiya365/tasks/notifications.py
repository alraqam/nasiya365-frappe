"""
Scheduled SMS notification tasks for Nasiya365.

Runs via hooks.py scheduler_events:
  - 9 AM  → send_due_today_reminders
  - 6 PM  → send_overdue_warnings
"""

import frappe
from frappe import _
from frappe.utils import today

from nasiya365.utils.sms_manager import SMSManager


def send_due_today_reminders():
    """Send one SMS per customer for payments due today (9 AM cron)."""
    frappe.logger().info("send_due_today_reminders: starting")

    # One row per customer — aggregate amount across all due-today rows
    rows = frappe.db.sql(
        """
        SELECT
            ip.customer,
            cp.full_name AS customer_name,
            SUM(isc.amount) AS total_due,
            COUNT(isc.name) AS installments_count
        FROM `tabInstallment Plan` ip
        INNER JOIN `tabInstallment Schedule` isc ON isc.parent = ip.name
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE ip.docstatus = 1
          AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан')
          AND isc.status IN ('Ожидает', 'Частично', 'Pending')
          AND isc.due_date = %s
        GROUP BY ip.customer, cp.full_name
        """,
        (today(),),
        as_dict=True,
    )

    sms = SMSManager()
    sent = 0
    for row in rows:
        phone = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": row.customer, "is_primary": 1},
            "phone_number",
        )
        if not phone:
            continue
        amount = f"{row.total_due:.2f}"
        message = _("Nasiya365: Сегодня срок оплаты {0} USD. Просим оплатить своевременно.").format(amount)
        if sms.send_sms(phone, message):
            sent += 1

    frappe.logger().info(f"send_due_today_reminders: sent {sent}/{len(rows)}")


def send_overdue_warnings():
    """Send one SMS per customer summarising all overdue debt (6 PM cron)."""
    frappe.logger().info("send_overdue_warnings: starting")

    # Deduplicated by customer: total overdue amount + max days overdue
    rows = frappe.db.sql(
        """
        SELECT
            ip.customer,
            cp.full_name AS customer_name,
            SUM(isc.amount - COALESCE(isc.paid_amount, 0)) AS total_overdue,
            MAX(DATEDIFF(%s, isc.due_date)) AS max_days
        FROM `tabInstallment Plan` ip
        INNER JOIN `tabInstallment Schedule` isc ON isc.parent = ip.name
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE ip.docstatus = 1
          AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан')
          AND isc.status = 'Просрочен'
        GROUP BY ip.customer, cp.full_name
        HAVING total_overdue > 0.001
        """,
        (today(),),
        as_dict=True,
    )

    sms = SMSManager()
    sent = 0
    for row in rows:
        phone = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": row.customer, "is_primary": 1},
            "phone_number",
        )
        if not phone:
            continue
        amount = f"{row.total_overdue:.2f}"
        days = int(row.max_days or 0)
        message = _("Nasiya365: Просрочка {0} дн., долг {1} USD. Просим погасить задолженность.").format(days, amount)
        if sms.send_sms(phone, message):
            sent += 1

    frappe.logger().info(f"send_overdue_warnings: sent {sent}/{len(rows)}")
