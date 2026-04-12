"""
Webhook callbacks for external services (SMS delivery status, etc.)
"""

import frappe


@frappe.whitelist(allow_guest=True)
def sms_delivery_status():
    """
    Eskiz SMS delivery status callback.
    Eskiz POSTs delivery receipts here after sending.

    Expected payload (Eskiz):
      {
        "message_id": "...",
        "status": "DELIVERED" | "UNDELIVERED" | "EXPIRED" | ...,
        "phone": "998901234567"
      }
    """
    try:
        data = frappe.request.get_json(silent=True) or {}
        msg_id = data.get("message_id") or data.get("messageId") or ""
        status = (data.get("status") or "").upper()
        phone = data.get("phone") or data.get("recipient") or ""

        if msg_id or phone:
            frappe.logger().info(
                f"SMS delivery callback: id={msg_id} status={status} phone={phone}"
            )

        # Log failures so we know when delivery is broken
        if status and status not in ("DELIVERED", "ACCEPTED", "SENT", ""):
            frappe.log_error(
                f"SMS delivery status={status} for msg_id={msg_id} phone={phone}",
                "SMS Delivery",
            )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "SMS Delivery Callback")

    return "OK"
