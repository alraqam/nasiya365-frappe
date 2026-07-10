"""Collections & Overdue — per-plan outstanding, overdue aging, and period collections."""

import frappe
from frappe import _
from frappe.utils import flt, cint, today, add_months, getdate

from nasiya365.api.profit import _branch_clause_for

_LIVE_PLAN_STATUSES = ("Активный", "Просрочен", "Завершен")
_OPEN_SCHEDULE_STATUSES = ("Ожидает", "Частично", "Pending")


def execute(filters=None):
    filters = filters or {}
    from_date = filters.get("from_date") or add_months(today(), -1)
    to_date = filters.get("to_date") or today()
    branch = filters.get("branch")
    only_overdue = cint(filters.get("only_overdue"))
    base_date = getdate(today())

    columns = [
        {"label": _("План"), "fieldname": "plan", "fieldtype": "Link", "options": "Installment Plan", "width": 150},
        {"label": _("Клиент"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
        {"label": _("Филиал"), "fieldname": "branch", "fieldtype": "Data", "width": 110},
        {"label": _("Сумма договора"), "fieldname": "total_amount", "fieldtype": "Currency", "width": 130},
        {"label": _("Оплачено"), "fieldname": "paid_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Остаток"), "fieldname": "remaining_balance", "fieldtype": "Currency", "width": 120},
        {"label": _("Просрочено"), "fieldname": "overdue_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Дней просрочки"), "fieldname": "days_overdue", "fieldtype": "Int", "width": 110},
        {"label": _("Собрано за период"), "fieldname": "collected_in_period", "fieldtype": "Currency", "width": 140},
    ]

    plan_branch_clause, plan_branch_params = _branch_clause_for("ip")
    expl = " AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch = %s)" if branch else ""
    expl_params = [branch] if branch else []
    in_status = ",".join(["%s"] * len(_LIVE_PLAN_STATUSES))
    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))

    plans = frappe.db.sql(
        f"""
        SELECT ip.name, ip.customer_name, ip.total_amount, ip.paid_amount,
               ip.remaining_balance,
               (SELECT branch FROM `tabSales Order` so WHERE so.name = ip.sales_order) AS branch,
               (
                   SELECT COALESCE(SUM(s.amount - COALESCE(s.paid_amount, 0)), 0)
                   FROM `tabInstallment Schedule` s
                   WHERE s.parent = ip.name
                     AND s.due_date < %s
                     AND (s.amount - COALESCE(s.paid_amount, 0)) > 0.001
                     AND IFNULL(s.status, '') IN ({in_open})
               ) AS overdue_amount,
               (
                   SELECT MAX(DATEDIFF(%s, s.due_date))
                   FROM `tabInstallment Schedule` s
                   WHERE s.parent = ip.name
                     AND s.due_date < %s
                     AND (s.amount - COALESCE(s.paid_amount, 0)) > 0.001
                     AND IFNULL(s.status, '') IN ({in_open})
               ) AS days_overdue,
               (
                   SELECT COALESCE(SUM(pt.amount), 0)
                   FROM `tabPayment Transaction` pt
                   WHERE pt.reference_doctype = 'Installment Plan'
                     AND pt.reference_name = ip.name
                     AND pt.docstatus < 2
                     AND pt.status = 'Завершен'
                     AND DATE(pt.payment_date) BETWEEN %s AND %s
               ) AS collected_in_period
        FROM `tabInstallment Plan` ip
        WHERE ip.docstatus = 1
          AND IFNULL(ip.status, '') IN ({in_status})
          AND IFNULL(ip.contract_status, '') != 'Отменен'
          {plan_branch_clause}
          {expl}
        ORDER BY days_overdue DESC, remaining_balance DESC
        """,
        (
            base_date, *_OPEN_SCHEDULE_STATUSES,
            base_date, base_date, *_OPEN_SCHEDULE_STATUSES,
            from_date, to_date,
            *_LIVE_PLAN_STATUSES, *plan_branch_params, *expl_params,
        ),
        as_dict=True,
    )

    data = []
    for p in plans:
        if only_overdue and flt(p.overdue_amount) <= 0.001:
            continue
        data.append({
            "plan": p.name,
            "customer_name": p.customer_name,
            "branch": p.branch,
            "total_amount": flt(p.total_amount),
            "paid_amount": flt(p.paid_amount),
            "remaining_balance": flt(p.remaining_balance),
            "overdue_amount": flt(p.overdue_amount),
            "days_overdue": cint(p.days_overdue),
            "collected_in_period": flt(p.collected_in_period),
        })

    total_outstanding = sum(d["remaining_balance"] for d in data)
    total_overdue = sum(d["overdue_amount"] for d in data)
    total_collected = sum(d["collected_in_period"] for d in data)
    report_summary = [
        {"label": _("Остаток портфеля"), "value": frappe.utils.fmt_money(total_outstanding, currency="USD")},
        {"label": _("Просрочено"), "value": frappe.utils.fmt_money(total_overdue, currency="USD"),
         "indicator": "Red" if total_overdue > 0 else "Green"},
        {"label": _("Собрано за период"), "value": frappe.utils.fmt_money(total_collected, currency="USD"),
         "indicator": "Green"},
    ]

    return columns, data, None, None, report_summary
