"""Profit & Loss Summary — revenue, COGS, margin, interest, expenses, net profit."""

import frappe
from frappe import _
from frappe.utils import flt, today, add_months

from nasiya365.api.profit import compute_profit


def _cost_recovery_rows(p, row):
    recognized_margin = flt(p["cash_margin"]) + flt(p["financed_margin"])
    recognized_interest = flt(p["interest_income"])
    recognized_profit = recognized_margin + recognized_interest
    cogs_recovered = flt(p["collected"]) - recognized_profit
    return [
        row(_("РАЗДЕЛ 1. Продажи за период (по факту продажи)"), 0, bold=1),
        row(_("Наличные — продажа"), p["sales_cash_revenue"], indent=1),
        row(_("Наличные — себестоимость"), -p["sales_cash_cogs"], indent=1),
        row(_("Наличные — маржа"), p["sales_cash_margin"], indent=1),
        row(_("Рассрочка — продажа"), p["sales_financed_revenue"], indent=1),
        row(_("Рассрочка — себестоимость"), -p["sales_financed_cogs"], indent=1),
        row(_("Рассрочка — маржа"), p["sales_financed_margin"], indent=1),
        row(_("Итого маржа товара"), p["sales_total_margin"], bold=1),
        row(_("Процентный доход (потенциальный)"), p["sales_interest"], indent=1),
        row(_("Потенциальная прибыль сделок"), p["potential_profit"], bold=1),
        row(_("РАЗДЕЛ 2. Признано за период (возмещение затрат)"), 0, bold=1),
        row(_("Собрано денег"), p["collected"], indent=1),
        row(_("Возмещение себестоимости"), -cogs_recovered, indent=1),
        row(_("Признанная прибыль"), recognized_profit, bold=1),
        row(_("    в т.ч. маржа"), recognized_margin, indent=1),
        row(_("    в т.ч. проценты"), recognized_interest, indent=1),
        row(_("Операционные расходы"), -p["expenses"], indent=1),
        row(_("ЧИСТАЯ ПРИБЫЛЬ (признанная)"), p["net_profit"], bold=1),
    ]


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

    if (p.get("profit_method") or "").startswith("Возмещение"):
        data = _cost_recovery_rows(p, row)
        report_summary = [
            {"label": _("Признанная прибыль"),
             "value": frappe.utils.fmt_money(p["net_profit"], currency="USD"),
             "indicator": "Green" if p["net_profit"] >= 0 else "Red"},
            {"label": _("Потенциал сделок периода"),
             "value": frappe.utils.fmt_money(p["potential_profit"], currency="USD")},
            {"label": _("Собрано"),
             "value": frappe.utils.fmt_money(p["collected"], currency="USD")},
        ]
        return columns, data, None, None, report_summary

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
