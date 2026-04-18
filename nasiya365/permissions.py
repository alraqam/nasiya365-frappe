import frappe

_UNRESTRICTED_ROLES = {"System Manager", "Nasiya365 Admin"}


def clear_branch_user_cache(doc, method=None):
    """Bust per-user branch cache when a Branch's user list changes."""
    for row in doc.get("branch_users") or []:
        if row.user:
            frappe.cache().delete_value(f"nasiya365:user_branches:{row.user}")


def _is_unrestricted(user: str) -> bool:
    return bool(_UNRESTRICTED_ROLES & set(frappe.get_roles(user)))


def _get_user_branches(user: str) -> list[str]:
    cache_key = f"nasiya365:user_branches:{user}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return cached
    rows = frappe.get_all(
        "Branch User",
        filters={"user": user, "is_active": 1},
        fields=["parent"],
        ignore_permissions=True,
    )
    branches = [r.parent for r in rows]
    frappe.cache().set_value(cache_key, branches, expires_in_sec=300)
    return branches


def _branch_in(doctype: str, branches: list[str]) -> str:
    quoted = ", ".join(f"'{b}'" for b in branches)
    return f"`tab{doctype}`.`branch` IN ({quoted})"


# ---------------------------------------------------------------------------
# permission_query_conditions — one function per DocType
# ---------------------------------------------------------------------------

def sales_order_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Sales Order", branches)


def installment_plan_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    quoted = ", ".join(f"'{b}'" for b in branches)
    return (
        "`tabInstallment Plan`.`sales_order` IN ("
        f"  SELECT `name` FROM `tabSales Order` WHERE `branch` IN ({quoted})"
        ")"
    )


def collector_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Collector", branches)


def cashbox_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Cashbox", branches)


def cash_handover_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Cash Handover", branches)


def expense_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Expense", branches)


def warehouse_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Warehouse", branches)


def payment_transaction_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    quoted = ", ".join(f"'{b}'" for b in branches)
    return (
        "`tabPayment Transaction`.`collected_by` IN ("
        f"  SELECT `name` FROM `tabCollector` WHERE `branch` IN ({quoted})"
        ")"
    )


# ---------------------------------------------------------------------------
# has_permission — document-level checks
# ---------------------------------------------------------------------------

def _doc_branch(doc) -> str | None:
    return getattr(doc, "branch", None)


def _check_branch(doc, user: str) -> bool:
    if _is_unrestricted(user):
        return True
    doc_branch = _doc_branch(doc)
    if not doc_branch:
        return True
    return doc_branch in _get_user_branches(user)


def has_sales_order_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_installment_plan_permission(doc, ptype: str, user: str) -> bool:
    if _is_unrestricted(user):
        return True
    branches = _get_user_branches(user)
    if not branches:
        return False
    so_branch = frappe.db.get_value("Sales Order", doc.sales_order, "branch")
    return so_branch in branches


def has_collector_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_cashbox_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_cash_handover_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_expense_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_warehouse_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_payment_transaction_permission(doc, ptype: str, user: str) -> bool:
    if _is_unrestricted(user):
        return True
    branches = _get_user_branches(user)
    if not branches:
        return False
    if not doc.collected_by:
        return True
    collector_branch = frappe.db.get_value("Collector", doc.collected_by, "branch")
    return collector_branch in branches
