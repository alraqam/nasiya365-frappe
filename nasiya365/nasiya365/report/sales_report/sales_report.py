"""Sales Report — unified cash + financed sales with margin, by period/branch."""

import frappe
from frappe import _
from frappe.utils import flt, today, add_months

from nasiya365.api.profit import _cogs_for_sale_item, _cogs_for_sales_order
from nasiya365.api.profit import _sales_order_user_clause, _branch_clause_for

_LIVE_PLAN_STATUSES = ("Активный", "Просрочен", "Завершен")


def execute(filters=None):
    filters = filters or {}
    from_date = filters.get("from_date") or add_months(today(), -1)
    to_date = filters.get("to_date") or today()
    branch = filters.get("branch")
    sale_type = filters.get("sale_type")  # "", "Наличные", "Рассрочка"
    imei = (filters.get("imei") or "").strip()
    # Escape LIKE wildcards so a literal % or _ in the term isn't treated as a pattern.
    imei_escaped = imei.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    imei_like = f"%{imei_escaped}%"

    columns = [
        {"label": _("Дата"), "fieldname": "sale_date", "fieldtype": "Date", "width": 100},
        {"label": _("Тип"), "fieldname": "sale_type", "fieldtype": "Data", "width": 100},
        {"label": _("Документ"), "fieldname": "doc_name", "fieldtype": "Dynamic Link",
         "options": "doc_type", "width": 150},
        {"label": _("Тип документа"), "fieldname": "doc_type", "fieldtype": "Data", "width": 1, "hidden": 1},
        {"label": _("Клиент"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
        {"label": _("Товар"), "fieldname": "product_name", "fieldtype": "Data", "width": 200},
        {"label": _("IMEI"), "fieldname": "imei", "fieldtype": "Data", "width": 140},
        {"label": _("Филиал"), "fieldname": "branch", "fieldtype": "Data", "width": 120},
        {"label": _("Продавец"), "fieldname": "salesperson", "fieldtype": "Data", "width": 130},
        {"label": _("Выручка"), "fieldname": "revenue", "fieldtype": "Currency", "width": 130},
        {"label": _("Себестоимость"), "fieldname": "cogs", "fieldtype": "Currency", "width": 130},
        {"label": _("Маржа"), "fieldname": "margin", "fieldtype": "Currency", "width": 130},
    ]

    data = []

    # ── Cash sales (Sales Orders not tied to a plan) ──
    if sale_type in (None, "", "Наличные"):
        so_branch = " AND so.branch = %s" if branch else ""
        so_branch_params = [branch] if branch else []
        user_clause, user_params = _sales_order_user_clause("so")
        cash_imei = (
            " AND EXISTS (SELECT 1 FROM `tabSales Order Item` soi"
            " WHERE soi.parent = so.name AND soi.imei LIKE %s)"
        ) if imei else ""
        cash = frappe.db.sql(
            f"""
            SELECT so.name, so.order_date, so.customer_name, so.branch,
                   so.salesperson, so.total_amount
            FROM `tabSales Order` so
            WHERE so.docstatus = 1
              AND DATE(so.order_date) BETWEEN %s AND %s
              AND NOT EXISTS (
                  SELECT 1 FROM `tabInstallment Plan` ip2
                  WHERE ip2.sales_order = so.name AND ip2.docstatus < 2
              )
              {so_branch}
              {cash_imei}
              {user_clause}
            ORDER BY so.order_date DESC
            """,
            (from_date, to_date, *so_branch_params, *([imei_like] if imei else []), *user_params),
            as_dict=True,
        )
        for so in cash:
            product, item_imei = frappe.db.get_value(
                "Sales Order Item", {"parent": so.name, "idx": 1},
                ["product_name", "imei"],
            ) or (None, None)
            revenue = flt(so.total_amount)
            cogs = _cogs_for_sales_order(so.name, so.order_date)
            data.append({
                "sale_date": so.order_date,
                "sale_type": _("Наличные"),
                "doc_name": so.name,
                "doc_type": "Sales Order",
                "customer_name": so.customer_name,
                "product_name": product or "—",
                "imei": item_imei or "",
                "branch": so.branch,
                "salesperson": so.salesperson,
                "revenue": revenue,
                "cogs": cogs,
                "margin": revenue - cogs,
            })

    # ── Financed sales (Installment Plans) ──
    if sale_type in (None, "", "Рассрочка"):
        in_status = ",".join(["%s"] * len(_LIVE_PLAN_STATUSES))
        plan_branch_clause, plan_branch_params = _branch_clause_for("ip")
        expl = " AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch = %s)" if branch else ""
        expl_params = [branch] if branch else []
        imei_sql = " AND ip.imei LIKE %s" if imei else ""
        plans = frappe.db.sql(
            f"""
            SELECT ip.name, ip.start_date, ip.customer_name, ip.imei,
                   ip.product_name, ip.principal_amount, ip.financed_amount,
                   ip.sales_order, ip.stock_entry,
                   (SELECT branch FROM `tabSales Order` so WHERE so.name = ip.sales_order) AS branch,
                   (SELECT salesperson FROM `tabSales Order` so WHERE so.name = ip.sales_order) AS salesperson
            FROM `tabInstallment Plan` ip
            WHERE ip.docstatus = 1
              AND IFNULL(ip.status, '') IN ({in_status})
              AND IFNULL(ip.contract_status, '') != 'Отменен'
              AND DATE(ip.start_date) BETWEEN %s AND %s
              {plan_branch_clause}
              {expl}
              {imei_sql}
            ORDER BY ip.start_date DESC
            """,
            (*_LIVE_PLAN_STATUSES, from_date, to_date, *plan_branch_params, *expl_params, *([imei_like] if imei else [])),
            as_dict=True,
        )
        for p in plans:
            revenue = flt(p.principal_amount) or flt(p.financed_amount)
            cogs = _cogs_for_sale_item(p.imei, p.stock_entry, p.start_date)
            data.append({
                "sale_date": p.start_date,
                "sale_type": _("Рассрочка"),
                "doc_name": p.name,
                "doc_type": "Installment Plan",
                "customer_name": p.customer_name,
                "product_name": p.product_name or "—",
                "imei": p.imei or "",
                "branch": p.branch,
                "salesperson": p.salesperson,
                "revenue": revenue,
                "cogs": cogs,
                "margin": revenue - cogs,
            })

    data.sort(key=lambda r: str(r["sale_date"]), reverse=True)

    total_revenue = sum(flt(r["revenue"]) for r in data)
    total_margin = sum(flt(r["margin"]) for r in data)
    report_summary = [
        {"label": _("Кол-во продаж"), "value": len(data)},
        {"label": _("Выручка"), "value": frappe.utils.fmt_money(total_revenue, currency="USD")},
        {"label": _("Маржа"), "value": frappe.utils.fmt_money(total_margin, currency="USD"),
         "indicator": "Green" if total_margin >= 0 else "Red"},
    ]

    return columns, data, None, None, report_summary
