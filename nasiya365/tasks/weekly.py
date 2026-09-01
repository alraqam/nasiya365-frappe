"""Еженедельные задачи Nasiya365."""

import frappe
from frappe.utils import add_days, flt, getdate, today

from nasiya365.finance import UNSETTLED_SCHEDULE_STATUSES, unsettled_schedule_predicate
from nasiya365.nasiya365.doctype.payment_allocation.payment_allocation import STATUS_ACTIVE

# Договоры, обязательства по которым ещё живы.
_LIVE_PLAN_STATUSES = ("Активный", "Просрочен")


def generate_collection_report(as_of=None):
    """Сборы за неделю: сколько ждали, сколько получили по этим обязательствам.

    Семь календарных дат: end − 6. Прежний код брал add_days(end, -7), а BETWEEN
    включает обе границы — получалось восемь дат.

    Ожидание считается ДО оплат периода: иначе платёж уменьшал бы и числитель, и
    знаменатель, и эффективность всегда стремилась бы к 100%.

    Собранным считается только то, что разнесено на обязательства периода.
    Наличные продажи, авансы и погашение старой просрочки в числитель не идут —
    раньше сюда попадали любые платежи за неделю.

    Разнесение берётся из журнала Payment Allocation. По ссылке строки на платёж
    его считать нельзя: ссылка одна, и строку, закрытую двумя платежами, зачитывал
    только последний — причём целиком. Платёж 40 внутри периода приносил в
    числитель все 100, включая 60, полученные раньше.
    """
    end_date = getdate(as_of) if as_of else getdate(today())
    start_date = add_days(end_date, -6)

    in_live = ",".join(["%s"] * len(_LIVE_PLAN_STATUSES))
    in_open = ",".join(["%s"] * len(UNSETTLED_SCHEDULE_STATUSES))
    open_pred = unsettled_schedule_predicate("isc", in_open)

    live_plan_where = f"""
          ip.docstatus = 1
          AND IFNULL(ip.status, '') IN ({in_live})
          AND IFNULL(ip.contract_status, '') != 'Отменен'
    """

    # Сколько по обязательствам периода ещё не получено.
    still_open = flt(
        frappe.db.sql(
            f"""
            SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
            FROM `tabInstallment Schedule` isc
            INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
            WHERE {live_plan_where}
              AND isc.due_date BETWEEN %s AND %s
            """,
            (*_LIVE_PLAN_STATUSES, start_date, end_date),
        )[0][0]
    )

    # Сколько по ним получено ИМЕННО в периоде — по журналу разноски.
    collected = flt(
        frappe.db.sql(
            f"""
            SELECT COALESCE(SUM(pa.allocated_amount), 0)
            FROM `tabPayment Allocation` pa
            INNER JOIN `tabInstallment Schedule` isc ON isc.name = pa.schedule_row
            INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
            INNER JOIN `tabPayment Transaction` pt ON pt.name = pa.payment_transaction
            WHERE {live_plan_where}
              AND pa.status = %s
              AND isc.due_date BETWEEN %s AND %s
              AND pt.docstatus = 1
              AND pt.status = 'Завершен'
              AND pt.payment_date BETWEEN %s AND %s
            """,
            (*_LIVE_PLAN_STATUSES, STATUS_ACTIVE, start_date, end_date,
             start_date, end_date),
        )[0][0]
    )

    # Ожидание = то, что ещё открыто, плюс то, что закрыли в этом же периоде.
    expected = still_open + collected

    overdue = flt(
        frappe.db.sql(
            f"""
            SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
            FROM `tabInstallment Schedule` isc
            INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
            WHERE ip.docstatus = 1
              AND IFNULL(ip.status, '') IN ({in_live})
              AND IFNULL(ip.contract_status, '') != 'Отменен'
              AND isc.due_date < %s
              AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
              AND {open_pred}
            """,
            (*_LIVE_PLAN_STATUSES, start_date, *UNSETTLED_SCHEDULE_STATUSES),
        )[0][0]
    )

    report = {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "days": 7,
        "expected": round(expected, 2),
        "collected_against_expected": round(collected, 2),
        "efficiency": round(collected / expected * 100, 2) if expected > 0.001 else 0,
        "overdue": round(overdue, 2),
    }

    frappe.logger().info(f"Weekly Report: {report}")
    # Доставка отчёта (почта менеджерам, сохранение) в объём исправления не входит —
    # аудит просил починить цифры. Задача по-прежнему только считает и логирует.
    return report
