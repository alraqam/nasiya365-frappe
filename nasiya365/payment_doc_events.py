"""
Document hooks for Payment Transaction (registered in hooks.py).
Keeps plan allocation on the hook path so it always runs after save.
"""

import frappe
from frappe.utils import cint, flt


def payment_transaction_after_insert(doc, method=None):
    from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
        allocate_payment_transaction_to_installment_plan,
    )

    allocate_payment_transaction_to_installment_plan(doc)


def payment_transaction_on_update(doc, method=None):
    if (doc.reference_doctype or "").strip() != "Installment Plan":
        return
    if not (doc.has_value_changed("reference_name") or doc.has_value_changed("reference_doctype")):
        return
    from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
        allocate_payment_transaction_to_installment_plan,
    )

    allocate_payment_transaction_to_installment_plan(doc)


def product_before_validate(doc, method=None):
    """Coerce BNPL numeric fields to avoid str/int comparisons on Product validate."""
    if not getattr(doc, "allow_installment", None):
        return

    try:
        min_pct = flt(getattr(doc, "min_down_payment_percent", 0))
    except Exception:
        min_pct = 0
    if min_pct <= 0:
        min_pct = 20.0
    doc.min_down_payment_percent = min_pct

    try:
        max_m = cint(getattr(doc, "max_installment_months", 0))
    except Exception:
        max_m = 0
    if max_m <= 0:
        max_m = 12
    doc.max_installment_months = max_m
