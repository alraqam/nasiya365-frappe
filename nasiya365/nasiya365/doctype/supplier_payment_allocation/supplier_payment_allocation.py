"""
Supplier Payment Allocation Child Table DocType

System-populated only (by allocate_supplier_payment) -- records exactly
which Stock Entry a Supplier Payment paid down and how much, so
cancellation can reverse the exact recorded amounts instead of
re-deriving "whichever payment currently matches" (the bug pattern
found in Payment Transaction / Installment Schedule's single-reference
design).
"""

from frappe.model.document import Document


class SupplierPaymentAllocation(Document):
    pass
