"""Profit & Loss Summary — revenue, COGS, margin, interest, expenses, net profit."""

import frappe
from frappe import _
from frappe.utils import flt, today, add_months

from nasiya365.api.profit import compute_profit


def execute(filters=None):
    filters = filters or {}
    from_date = filters.get("from_date") or add_months(today(), -1)
    to_date = filters.get("to_date") or today()
    branch = filters.get("branch")

    p = compute_profit(from_date, to_date, branch)

    columns = [
        {"label": _("Показатель"), "fieldname": "metric", "fieldtype": "Data", "width": 320},
        {"label": _("Сумма (USD)"), "fieldname": "amount", "fieldtype": "Currency", "width": 180},
    ]

    def row(metric, amount, bold=0, indent=0):
        return {"metric": ("    " * indent) + metric, "amount": flt(amount), "bold": bold}

    data = [
        row(_("Наличные продажи — выручка"), p["cash_revenue"], indent=1),
        row(_("Наличные продажи — себестоимость"), -p["cash_cogs"], indent=1),
        row(_("Маржа с наличных продаж"), p["cash_margin"], bold=1),
        row(_("Рассрочка — выручка (товар)"), p["financed_revenue"], indent=1),
        row(_("Рассрочка — себестоимость"), -p["financed_cogs"], indent=1),
        row(_("Маржа с рассрочки"), p["financed_margin"], bold=1),
        row(_("Итого маржа с товаров"), p["total_margin"], bold=1),
        row(_("Процентный доход") + (" (учтён)" if p["interest_in_profit"] else " (не входит в базу)"),
            p["interest_income"], indent=1),
        row(_("Валовая прибыль"), p["gross_profit"], bold=1),
        row(_("Операционные расходы") + ("" if p["expenses_in_profit"] else " (не входят в базу)"),
            -p["expenses"], indent=1),
        row(_("ЧИСТАЯ ПРИБЫЛЬ") + f" ({p['profit_basis']} · {p.get('profit_method', '')})",
            p["net_profit"], bold=1),
    ]

    report_summary = [
        {"label": _("Чистая прибыль"), "value": frappe.utils.fmt_money(p["net_profit"], currency="USD"),
         "indicator": "Green" if p["net_profit"] >= 0 else "Red"},
        {"label": _("Маржа с товаров"), "value": frappe.utils.fmt_money(p["total_margin"], currency="USD")},
        {"label": _("Процентный доход"), "value": frappe.utils.fmt_money(p["interest_income"], currency="USD")},
        {"label": _("Расходы"), "value": frappe.utils.fmt_money(p["expenses"], currency="USD")},
    ]

    return columns, data, None, None, report_summary
