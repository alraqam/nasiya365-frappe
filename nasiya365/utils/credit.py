"""Shared credit / BNPL limit helpers."""

import frappe
from frappe.utils import cint


def is_customer_credit_limit_enforced():
    """
    When False (default), Installment Plan / Sales Order do not block on customer.available_limit.
    Turn on in Merchant Settings to enforce per-customer credit_limit again.
    """
    try:
        return cint(frappe.db.get_single_value("Merchant Settings", "enforce_customer_credit_limit"))
    except Exception:
        return 0
