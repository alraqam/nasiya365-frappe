"""Suppliers Payable — outstanding balance owed to each supplier, per branch."""

import frappe
from frappe import _
from frappe.utils import cint, flt, today, getdate


def _stock_entry_branch_clause(alias="w"):
    """Restrict to warehouses in the caller's branches (fail-closed). Empty
    fragment for unrestricted users, same convention as api.profit._branch_clause_for
    but joined via warehouse.branch since Stock Entry has no direct branch column."""
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    user = frappe.session.user
    if _is_unrestricted(user):
        return ("", [])
    branches = _get_user_branches(user)
    if not branches:
        return (" AND 1=0", [])
    placeholders = ",".join(["%s"] * len(branches))
    return (f" AND {alias}.branch IN ({placeholders})", list(branches))


def execute(filters=None):
    filters = filters or {}
    branch = filters.get("branch")
    only_outstanding = cint(filters.get("only_outstanding"))
    base_date = getdate(today())

    columns = [
        {"label": _("Поставщик"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 200},
        {"label": _("Филиал"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 120},
        {"label": _("Закуплено"), "fieldname": "total_purchased", "fieldtype": "Currency", "width": 130},
        {"label": _("Оплачено"), "fieldname": "total_paid", "fieldtype": "Currency", "width": 130},
        {"label": _("Долг"), "fieldname": "balance_due", "fieldtype": "Currency", "width": 130},
        {"label": _("Дней с самой старой неоплаты"), "fieldname": "oldest_unpaid_days", "fieldtype": "Int", "width": 170},
    ]

    branch_clause, branch_params = _stock_entry_branch_clause("w")
    expl = " AND w.branch = %s" if branch else ""
    expl_params = [branch] if branch else []

    rows = frappe.db.sql(
        f"""
        SELECT
            se.supplier,
            w.branch,
            SUM(se.total_value) AS total_purchased,
            SUM(se.paid_amount) AS total_paid,
            SUM(se.balance_due) AS balance_due,
            MAX(CASE WHEN se.balance_due > 0.001 THEN DATEDIFF(%s, se.posting_date) ELSE NULL END) AS oldest_unpaid_days
        FROM `tabStock Entry` se
        INNER JOIN `tabWarehouse` w ON w.name = se.warehouse
        WHERE se.docstatus = 1
          AND se.entry_type = 'Поступление'
          AND se.supplier IS NOT NULL AND se.supplier != ''
          {branch_clause}
          {expl}
        GROUP BY se.supplier, w.branch
        ORDER BY balance_due DESC, oldest_unpaid_days DESC
        """,
        (base_date, *branch_params, *expl_params),
        as_dict=True,
    )

    data = []
    for r in rows:
        if only_outstanding and flt(r.balance_due) <= 0.001:
            continue
        data.append({
            "supplier": r.supplier,
            "branch": r.branch,
            "total_purchased": flt(r.total_purchased),
            "total_paid": flt(r.total_paid),
            "balance_due": flt(r.balance_due),
            "oldest_unpaid_days": cint(r.oldest_unpaid_days) if r.oldest_unpaid_days is not None else 0,
        })

    total_purchased = sum(d["total_purchased"] for d in data)
    total_paid = sum(d["total_paid"] for d in data)
    total_due = sum(d["balance_due"] for d in data)
    report_summary = [
        {"label": _("Всего закуплено"), "value": frappe.utils.fmt_money(total_purchased, currency="USD")},
        {"label": _("Всего оплачено поставщикам"), "value": frappe.utils.fmt_money(total_paid, currency="USD")},
        {"label": _("Долг поставщикам"), "value": frappe.utils.fmt_money(total_due, currency="USD"),
         "indicator": "Red" if total_due > 0 else "Green"},
    ]

    return columns, data, None, None, report_summary
