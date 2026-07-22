"""Приход кассы по периодам — сколько денег в кассе относится к прошлым датам.

Отвечает на вопрос «из сегодняшнего прихода сколько за прошлый месяц»: деньги
физически попадают в текущую открытую кассу, а дата факта живёт на платеже
(`payment_date`). Отчёт сводит две картины в одну таблицу.

Ничего не хранит и не денормализует — дата факта и метка «задним числом»
вычисляются по связи `Cashbox Transaction` → `Payment Transaction`.
"""

import frappe
from frappe import _
from frappe.utils import flt, get_first_day, getdate, today

# Курс по умолчанию, если у строки кассы он не проставлен (как в Cashbox.calculate_totals).
_DEFAULT_RATE = 12200.0


def execute(filters=None):
    filters = filters or {}
    from_date = filters.get("from_date") or get_first_day(today())
    to_date = filters.get("to_date") or today()

    rows = _fetch(from_date, to_date, filters)
    return _columns(), rows, None, None, _summary(rows)


def _user_branch_clause():
    """Ограничение по филиалам вызывающего (пусто = без ограничений)."""
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    user = frappe.session.user
    if _is_unrestricted(user):
        return "", []

    branches = _get_user_branches(user) or []
    if not branches:
        return " AND 1=0", []

    placeholders = ",".join(["%s"] * len(branches))
    return f" AND cb.branch IN ({placeholders})", list(branches)


def _to_usd(row):
    """USD-эквивалент строки кассы (UZS переводим по курсу строки)."""
    amount = flt(row.get("amount"))
    currency = (row.get("currency") or "USD").strip().upper()
    if currency == "USD":
        return amount

    rate = flt(row.get("exchange_rate")) or _DEFAULT_RATE
    return amount / rate if rate > 0 else 0.0


def _fetch(from_date, to_date, filters):
    conditions = []
    params = [from_date, to_date]

    if filters.get("cashbox"):
        conditions.append("AND ct.parent = %s")
        params.append(filters["cashbox"])

    if filters.get("branch"):
        conditions.append("AND cb.branch = %s")
        params.append(filters["branch"])

    user_clause, user_params = _user_branch_clause()
    params.extend(user_params)
    extra = " ".join(conditions) + user_clause

    rows = frappe.db.sql(
        f"""
        SELECT
            ct.timestamp        AS cash_time,
            ct.parent           AS cashbox,
            cb.branch           AS branch,
            ct.reference_name   AS payment,
            ct.payment_method   AS payment_method,
            ct.amount           AS amount,
            ct.currency         AS currency,
            ct.exchange_rate    AS exchange_rate,
            pt.payment_date     AS fact_date,
            DATE_FORMAT(pt.payment_date, '%%Y-%%m')            AS period,
            CASE WHEN DATE(pt.payment_date) < DATE(ct.timestamp)
                 THEN 1 ELSE 0 END                             AS is_backdated,
            CASE WHEN DATE_FORMAT(pt.payment_date, '%%Y-%%m')
                    < DATE_FORMAT(ct.timestamp, '%%Y-%%m')
                 THEN 1 ELSE 0 END                             AS is_prior_month
        FROM `tabCashbox Transaction` ct
        JOIN `tabCashbox` cb             ON cb.name = ct.parent
        JOIN `tabPayment Transaction` pt ON pt.name = ct.reference_name
        WHERE ct.parenttype = 'Cashbox'
          AND ct.reference_doctype = 'Payment Transaction'
          AND ct.transaction_type = 'Приход'
          AND pt.docstatus < 2
          AND DATE(ct.timestamp) BETWEEN %s AND %s
          {extra}
        ORDER BY ct.timestamp DESC, ct.parent
        """,
        params,
        as_dict=True,
    )

    for row in rows:
        row["amount_usd"] = round(_to_usd(row), 2)
    return rows


def _columns():
    return [
        {"label": _("Дата кассы"), "fieldname": "cash_time", "fieldtype": "Datetime", "width": 160},
        {"label": _("Касса"), "fieldname": "cashbox", "fieldtype": "Link", "options": "Cashbox", "width": 140},
        {"label": _("Филиал"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 120},
        {"label": _("Платёж"), "fieldname": "payment", "fieldtype": "Link", "options": "Payment Transaction", "width": 150},
        {"label": _("Метод"), "fieldname": "payment_method", "fieldtype": "Data", "width": 130},
        {"label": _("Сумма"), "fieldname": "amount", "fieldtype": "Float", "width": 100},
        {"label": _("Валюта"), "fieldname": "currency", "fieldtype": "Data", "width": 75},
        {"label": _("USD"), "fieldname": "amount_usd", "fieldtype": "Currency", "options": "USD", "width": 110},
        {"label": _("Дата факта"), "fieldname": "fact_date", "fieldtype": "Date", "width": 110},
        {"label": _("Период"), "fieldname": "period", "fieldtype": "Data", "width": 90},
        {"label": _("Задним числом"), "fieldname": "is_backdated", "fieldtype": "Check", "width": 130},
    ]


def _summary(rows):
    total = sum(flt(r["amount_usd"]) for r in rows)
    backdated = sum(flt(r["amount_usd"]) for r in rows if r.get("is_backdated"))
    prior_month = sum(flt(r["amount_usd"]) for r in rows if r.get("is_prior_month"))

    # Разбивка прошлых месяцев: «2026-05 $50 · 2026-06 $50»
    by_period = {}
    for r in rows:
        if r.get("is_prior_month"):
            by_period[r.get("period") or "—"] = by_period.get(r.get("period") or "—", 0) + flt(r["amount_usd"])
    breakdown = " · ".join(
        f"{p} {frappe.utils.fmt_money(v, currency='USD')}" for p, v in sorted(by_period.items())
    ) or _("нет")

    return [
        {
            "label": _("Всего приход"),
            "value": frappe.utils.fmt_money(total, currency="USD"),
            "indicator": "Blue",
        },
        {
            "label": _("Задним числом"),
            "value": frappe.utils.fmt_money(backdated, currency="USD"),
            "indicator": "Orange" if backdated else "Green",
        },
        {
            "label": _("Из прошлых месяцев"),
            "value": frappe.utils.fmt_money(prior_month, currency="USD"),
            "indicator": "Orange" if prior_month else "Green",
        },
        {
            "label": _("Разбивка по месяцам"),
            "value": breakdown,
        },
    ]
