import frappe
from frappe import _
from frappe.utils import cint

_UNRESTRICTED_ROLES = {"System Manager", "Nasiya365 Admin"}


# ---------------------------------------------------------------------------
# Public helpers used by whitelist endpoints to gate access
# ---------------------------------------------------------------------------

def require_branch_access(doctype: str, name: str, ptype: str = "read") -> None:
    """Throw PermissionError if the current user can't access this doc.

    Use at the top of @frappe.whitelist() endpoints that take a document name
    parameter and would otherwise bypass list-level branch filtering.
    """
    if not name:
        return
    if not frappe.db.exists(doctype, name):
        frappe.throw(_("{0} не найден").format(_(doctype)))
    if not frappe.has_permission(doctype, ptype=ptype, doc=name):
        frappe.throw(_("Нет доступа к {0}").format(_(doctype)), frappe.PermissionError)


def admin_only_for_submitted_delete(doc) -> None:
    """Block non-admin users from deleting docstatus=1/2 records.

    Call from on_trash hooks of submittable DocTypes. Branch Manager can delete
    drafts in their own branch (Frappe list permissions handle that); deletion
    of submitted/cancelled records requires Nasiya365 Admin or System Manager.
    """
    if cint(getattr(doc, "docstatus", 0)) in (1, 2):
        if not _is_unrestricted(frappe.session.user):
            frappe.throw(
                _(
                    "Удаление проведённых или отменённых документов разрешено только администратору."
                ),
                frappe.PermissionError,
            )


# Map Russian Branch User.role values to Frappe System Role names.
_BRANCH_ROLE_TO_SYSTEM_ROLE = {
    "Менеджер": "Branch Manager",
    "Продавец": "Salesperson",
    "Кассир": "Cashier",
    "Коллектор": "Collector",
    "Кредитный офицер": "Credit Officer",
    "Менеджер склада": "Warehouse Manager",
}
# Reverse-lookup used when cleaning up orphan system roles after Branch User removals.
_SYSTEM_ROLES_GRANTED_BY_BRANCH_USER = set(_BRANCH_ROLE_TO_SYSTEM_ROLE.values())


def clear_branch_user_cache(doc, method=None):
    """Bust per-user branch cache when a Branch's user list changes.

    Also re-syncs the Frappe System Roles on each user to match the union of
    their active Branch User.role assignments.
    """
    affected_users = set()
    for row in doc.get("branch_users") or []:
        if row.user:
            affected_users.add(row.user)
            frappe.cache().delete_value(f"nasiya365:user_branches:{row.user}")
    for user in affected_users:
        sync_user_system_roles_from_branch_users(user)


def sync_user_system_roles_from_branch_users(user: str) -> None:
    """Ensure the user has exactly the Frappe System Roles granted by their
    active Branch User rows — no more, no less.

    - Adds any missing system role implied by an active Branch User.role.
    - Removes any system role that was previously granted by a Branch User but
      is no longer backed by an active row (i.e., orphan role cleanup).
    - Preserves all other roles assigned directly to the User doc (e.g. System
      Manager, Nasiya365 Admin), since those are not granted via Branch User.
    """
    if not user or user in ("Administrator", "Guest"):
        return
    rows = frappe.get_all(
        "Branch User",
        filters={"user": user, "is_active": 1},
        fields=["role"],
        ignore_permissions=True,
    )
    granted_now = {
        _BRANCH_ROLE_TO_SYSTEM_ROLE.get((r.role or "").strip())
        for r in rows
        if _BRANCH_ROLE_TO_SYSTEM_ROLE.get((r.role or "").strip())
    }
    user_doc = frappe.get_doc("User", user)
    current = {r.role for r in user_doc.get("roles") or []}
    target = (current - _SYSTEM_ROLES_GRANTED_BY_BRANCH_USER) | granted_now
    if target == current:
        return
    user_doc.set(
        "roles",
        [{"role": role} for role in sorted(target) if role],
    )
    user_doc.save(ignore_permissions=True)


def auto_assign_manager_branch(login_manager):
    """On login, if the user has no branch assignments, add them as Менеджер
    to any branch whose `manager` field points to their account."""
    user = getattr(login_manager, "user", None) or frappe.session.user
    if not user or user in ("Administrator", "Guest"):
        return
    if _is_unrestricted(user):
        return
    # Skip if already assigned to at least one active branch
    existing = frappe.get_all(
        "Branch User",
        filters={"user": user, "is_active": 1},
        fields=["name"],
        limit=1,
        ignore_permissions=True,
    )
    if existing:
        return
    # Find branches where this user is set as manager
    managed = frappe.get_all(
        "Branch",
        filters={"manager": user, "is_active": 1},
        fields=["name"],
        ignore_permissions=True,
    )
    for row in managed:
        branch = frappe.get_doc("Branch", row.name)
        already = any(r.user == user for r in branch.get("branch_users") or [])
        if not already:
            branch.append("branch_users", {"user": user, "role": "Менеджер", "is_active": 1})
            branch.save(ignore_permissions=True)
    if managed:
        frappe.cache().delete_value(f"nasiya365:user_branches:{user}")
        # Ensure the user has the Branch Manager system role so DocType permissions apply
        user_doc = frappe.get_doc("User", user)
        has_role = any(r.role == "Branch Manager" for r in user_doc.get("roles") or [])
        if not has_role:
            user_doc.append("roles", {"role": "Branch Manager"})
            user_doc.save(ignore_permissions=True)


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


def _escaped_branch_list(branches: list[str]) -> str:
    """Return `'a','b','c'` with each value escaped by frappe.db.escape to prevent SQL injection."""
    return ", ".join(frappe.db.escape(b) for b in branches)


def _branch_in(doctype: str, branches: list[str]) -> str:
    return f"`tab{doctype}`.`branch` IN ({_escaped_branch_list(branches)})"


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


def trade_in_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Trade In", branches)


def installment_plan_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    escaped = _escaped_branch_list(branches)
    return (
        "`tabInstallment Plan`.`sales_order` IN ("
        f"  SELECT `name` FROM `tabSales Order` WHERE `branch` IN ({escaped})"
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
    escaped = _escaped_branch_list(branches)
    # Filter via collector.branch OR via linked installment plan's sales_order.branch.
    # Payments with no collector AND no plan reference are hidden from branch-scoped users.
    return (
        "("
        f"  `tabPayment Transaction`.`collected_by` IN ("
        f"    SELECT `name` FROM `tabCollector` WHERE `branch` IN ({escaped})"
        f"  )"
        "  OR ("
        "    `tabPayment Transaction`.`reference_doctype` = 'Installment Plan'"
        "    AND `tabPayment Transaction`.`reference_name` IN ("
        "      SELECT ip.`name` FROM `tabInstallment Plan` ip"
        "      INNER JOIN `tabSales Order` so ON so.`name` = ip.`sales_order`"
        f"      WHERE so.`branch` IN ({escaped})"
        "    )"
        "  )"
        ")"
    )


def customer_profile_query(user: str = None) -> str:
    """Filter customers to those with at least one Sales Order in caller's branches."""
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    escaped = _escaped_branch_list(branches)
    return (
        "EXISTS ("
        "  SELECT 1 FROM `tabSales Order` so"
        "  WHERE so.`customer` = `tabCustomer Profile`.`name`"
        f"  AND so.`branch` IN ({escaped})"
        ")"
    )


def stock_entry_query(user: str = None) -> str:
    """Filter stock entries by warehouse → branch."""
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    escaped = _escaped_branch_list(branches)
    return (
        "`tabStock Entry`.`warehouse` IN ("
        f"  SELECT `name` FROM `tabWarehouse` WHERE `branch` IN ({escaped})"
        ")"
    )


def supplier_payment_query(user: str = None) -> str:
    user = user or frappe.session.user
    if _is_unrestricted(user):
        return ""
    branches = _get_user_branches(user)
    if not branches:
        return "1=0"
    return _branch_in("Supplier Payment", branches)


# ---------------------------------------------------------------------------
# has_permission — document-level checks
# ---------------------------------------------------------------------------

def _doc_branch(doc) -> str | None:
    return getattr(doc, "branch", None)


def _check_branch(doc, user: str) -> bool:
    """Fail-closed branch check for DocTypes with a branch field."""
    if _is_unrestricted(user):
        return True
    doc_branch = _doc_branch(doc)
    if not doc_branch:
        return False
    return doc_branch in _get_user_branches(user)


def has_sales_order_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_trade_in_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)


def has_installment_plan_permission(doc, ptype: str, user: str) -> bool:
    if _is_unrestricted(user):
        return True
    branches = _get_user_branches(user)
    if not branches:
        return False
    if not doc.sales_order:
        return False
    so_branch = frappe.db.get_value("Sales Order", doc.sales_order, "branch")
    return bool(so_branch) and so_branch in branches


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
    # Resolve via collector first (direct).
    if doc.collected_by:
        collector_branch = frappe.db.get_value("Collector", doc.collected_by, "branch")
        if collector_branch and collector_branch in branches:
            return True
    # Fallback: via linked installment plan → sales_order → branch.
    if doc.reference_doctype == "Installment Plan" and doc.reference_name:
        so = frappe.db.get_value("Installment Plan", doc.reference_name, "sales_order")
        if so:
            so_branch = frappe.db.get_value("Sales Order", so, "branch")
            if so_branch and so_branch in branches:
                return True
    return False


def has_customer_profile_permission(doc, ptype: str, user: str) -> bool:
    """Customer is accessible only if they have an order in user's branch."""
    if _is_unrestricted(user):
        return True
    branches = _get_user_branches(user)
    if not branches:
        return False
    return bool(
        frappe.db.exists(
            "Sales Order",
            {"customer": doc.name, "branch": ["in", branches]},
        )
    )


def has_stock_entry_permission(doc, ptype: str, user: str) -> bool:
    if _is_unrestricted(user):
        return True
    branches = _get_user_branches(user)
    if not branches:
        return False
    if not doc.warehouse:
        return False
    wh_branch = frappe.db.get_value("Warehouse", doc.warehouse, "branch")
    return bool(wh_branch) and wh_branch in branches


def has_supplier_payment_permission(doc, ptype: str, user: str) -> bool:
    return _check_branch(doc, user)
