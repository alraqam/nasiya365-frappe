"""
Document hooks for Payment Transaction (registered in hooks.py).
Keeps plan allocation on the hook path so it always runs after save.
"""

import frappe


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
