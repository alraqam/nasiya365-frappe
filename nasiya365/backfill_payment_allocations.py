"""Восстановление журнала разноски для платежей, проведённых до его появления.

Запускать вручную, в два шага:

    bench --site <site> execute nasiya365.backfill_payment_allocations.dry_run
    bench --site <site> execute nasiya365.backfill_payment_allocations.apply

Сбросом строк графика историю восстановить нельзя: у строки одна ссылка на
платёж, и после двух оплат она указывает только на последнюю. Поэтому платежи
каждого плана проигрываются заново в хронологическом порядке тем же кодом,
что и разносит их в бою (apply_payment), а полученный итог сверяется с тем, что
лежит в базе.

Планы, где проигрывание не сошлось с фактом, в журнал НЕ пишутся: расхождение
означает, что реальная история отличалась от FIFO — например, строки правили
руками. Такие планы попадают в отчёт, и решение по ним принимает человек.
"""

import json

import frappe
from frappe.utils import flt

from nasiya365.nasiya365.doctype.payment_allocation import payment_allocation

_TOLERANCE = 0.01


def _plans_with_payments() -> list:
    return frappe.db.sql_list(
        """
        SELECT DISTINCT pt.reference_name
        FROM `tabPayment Transaction` pt
        WHERE pt.reference_doctype = 'Installment Plan'
          AND pt.docstatus = 1
          AND IFNULL(pt.status, '') = 'Завершен'
          AND pt.reference_name IS NOT NULL
        """
    )


def _payments_of(plan_name) -> list:
    """Платежи плана в том порядке, в каком они поступали."""
    return frappe.db.sql(
        """
        SELECT name, amount, payment_date
        FROM `tabPayment Transaction`
        WHERE reference_doctype = 'Installment Plan'
          AND reference_name = %s
          AND docstatus = 1
          AND IFNULL(status, '') = 'Завершен'
        ORDER BY payment_date ASC, creation ASC
        """,
        (plan_name,),
        as_dict=True,
    )


def _replay(plan_name):
    """Проиграть платежи плана заново. Возвращает (аллокации, расхождения).

    Работает на копии документа в памяти: строки графика обнуляются, платежи
    применяются по порядку, ничего не сохраняется.
    """
    plan = frappe.get_doc("Installment Plan", plan_name)
    actual = {row.name: flt(row.paid_amount) for row in plan.schedule}

    # Проигрывание идёт по состоянию НА МОМЕНТ ОПЛАТЫ, а не по нынешнему: план,
    # сегодня завершённый или списанный, тогда был активен, и apply_payment
    # отказался бы его трогать. Документ не сохраняется — правка живёт в памяти.
    plan.status = "Активный"

    for row in plan.schedule:
        row.paid_amount = 0
        row.status = "Ожидает"
        row.paid_date = None

    allocations = []
    for payment in _payments_of(plan_name):
        plan.apply_payment(flt(payment.amount), payment_transaction=payment.name,
                           payment_date=payment.payment_date, record_ledger=False)
        for row_name, amount in getattr(plan, "_nasiya_last_allocations", []):
            allocations.append((payment.name, row_name, flt(amount)))

    replayed = {row.name: flt(row.paid_amount) for row in plan.schedule}
    mismatches = [
        {"row": name, "было": actual.get(name, 0.0), "проигрыш": replayed.get(name, 0.0)}
        for name in actual
        if abs(actual.get(name, 0.0) - replayed.get(name, 0.0)) > _TOLERANCE
    ]
    return allocations, mismatches


def dry_run(verbose=True) -> dict:
    """Проиграть все планы и показать, где проигрывание разошлось с фактом."""
    plans = _plans_with_payments()
    ok, broken, total_allocations = [], [], 0

    for plan_name in plans:
        try:
            allocations, mismatches = _replay(plan_name)
        except Exception as exc:
            broken.append({"plan": plan_name, "ошибка": str(exc)[:200]})
            continue
        if mismatches:
            broken.append({"plan": plan_name, "расхождения": mismatches})
        else:
            ok.append(plan_name)
            total_allocations += len(allocations)

    report = {
        "планов всего": len(plans),
        "сойдётся": len(ok),
        "не сошлось": len(broken),
        "записей к созданию": total_allocations,
        "проблемные": broken[:20],
    }
    if verbose:
        frappe.logger().info(f"backfill_payment_allocations dry_run: {report}")
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return report


def apply() -> dict:
    """Записать журнал по тем планам, где проигрывание сошлось с фактом."""
    plans = _plans_with_payments()
    written, skipped = 0, []

    for plan_name in plans:
        try:
            allocations, mismatches = _replay(plan_name)
        except Exception as exc:
            skipped.append({"plan": plan_name, "ошибка": str(exc)[:200]})
            continue
        if mismatches:
            skipped.append({"plan": plan_name, "расхождения": mismatches})
            continue

        by_payment = {}
        for payment_name, row_name, amount in allocations:
            by_payment.setdefault(payment_name, []).append((row_name, amount))
        for payment_name, rows in by_payment.items():
            written += payment_allocation.record(payment_name, plan_name, rows)

    frappe.db.commit()
    result = {"записей создано": written, "планов пропущено": len(skipped),
              "пропущенные": skipped[:20]}
    frappe.logger().info(f"backfill_payment_allocations apply: {result}")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return result
