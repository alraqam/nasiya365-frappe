"""Shareholder Distribution — net profit for the period split among shareholders."""

import frappe
from frappe import _
from frappe.utils import flt, today, add_months

from nasiya365.api.profit import compute_profit, compute_shareholder_split


def execute(filters=None):
    filters = filters or {}
    from_date = filters.get("from_date") or add_months(today(), -1)
    to_date = filters.get("to_date") or today()
    branch = filters.get("branch")

    profit = compute_profit(from_date, to_date, branch)
    net = profit["net_profit"]
    split = compute_shareholder_split(net)

    columns = [
        {"label": _("Акционер"), "fieldname": "shareholder", "fieldtype": "Data", "width": 240},
        {"label": _("Доля %"), "fieldname": "share_percent", "fieldtype": "Percent", "width": 120},
        {"label": _("Доля прибыли (USD)"), "fieldname": "amount", "fieldtype": "Currency", "width": 200},
    ]

    data = [
        {"shareholder": s["shareholder"], "share_percent": s["share_percent"], "amount": s["amount"]}
        for s in split
    ]

    if not data:
        frappe.msgprint(_("Акционеры не настроены. Добавьте их в «Настройки магазина» → «Акционеры»."))

    report_summary = [
        {"label": _("Чистая прибыль к распределению"),
         "value": frappe.utils.fmt_money(net, currency="USD"),
         "indicator": "Green" if net >= 0 else "Red"},
        {"label": _("Модель"),
         "value": (frappe.get_single("Merchant Settings").shareholder_split_model or "—")},
        {"label": _("Распределено"),
         "value": frappe.utils.fmt_money(sum(flt(d["amount"]) for d in data), currency="USD")},
    ]

    return columns, data, None, None, report_summary
