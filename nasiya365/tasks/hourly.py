"""
Hourly Scheduled Tasks for Nasiya365
"""

import frappe


def sync_payment_status():
    """
    Sync payment status with payment gateways.
    Checks pending transactions and updates their status.
    """
    frappe.logger().info("Running: sync_payment_status")
    
    # Get all pending payment transactions
    pending_payments = frappe.get_all(
        "Payment Transaction",
        filters={"status": "Pending"},
        fields=["name", "transaction_id", "payment_method", "amount"]
    )
    
    for payment in pending_payments:
        # Check status with respective payment gateway
        if payment.payment_method == "Click":
            # check_click_status(payment)
            pass
        elif payment.payment_method == "Payme":
            # check_payme_status(payment)
            pass
    
    frappe.logger().info(f"Checked {len(pending_payments)} pending payments")
