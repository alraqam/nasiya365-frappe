"""Еженедельные задачи Nasiya365."""

import frappe
from frappe.utils import add_days, flt, getdate, today

from nasiya365.finance import UNSETTLED_SCHEDULE_STATUSES, unsettled_schedule_predicate

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

    ОГРАНИЧЕНИЕ: разнесение берётся по ссылке Installment Schedule.payment_transaction,
    а она хранит один платёж на строку. Строку, закрытую двумя платежами, зачтёт
    только последний. Точным числитель станет после журнала Payment Allocation
    (стадия 3 плана исправлений).
    """
    end_date = getdate(as_of) if as_of else getdate(today())
    start_date = add_days(end_date, -6)

    in_live = ",".join(["%s"] * len(_LIVE_PLAN_STATUSES))
    in_open = ",".join(["%s"] * len(UNSETTLED_SCHEDULE_STATUSES))
    open_pred = unsettled_schedule_predicate("isc", in_open)

    # Обязательства со сроком в периоде и оплата по ним, полученная в периоде.
    row = frappe.db.sql(
        f"""
        SELECT
            COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0) AS still_open,
            COALESCE(SUM(CASE WHEN pt.payment_date BETWEEN %s AND %s
                              THEN COALESCE(isc.paid_amount, 0) ELSE 0 END), 0) AS paid_in_period
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        LEFT JOIN `tabPayment Transaction` pt
               ON pt.name = isc.payment_transaction
              AND pt.docstatus = 1
              AND pt.status = 'Завершен'
        WHERE ip.docstatus = 1
          AND IFNULL(ip.status, '') IN ({in_live})
          AND IFNULL(ip.contract_status, '') != 'Отменен'
          AND isc.due_date BETWEEN %s AND %s
        """,
        (start_date, end_date, *_LIVE_PLAN_STATUSES, start_date, end_date),
        as_dict=True,
    )[0]

    collected = flt(row.paid_in_period)
    # Ожидание = то, что ещё открыто, плюс то, что закрыли в этом же периоде.
    expected = flt(row.still_open) + collected

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
