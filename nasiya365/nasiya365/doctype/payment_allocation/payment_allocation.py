"""Журнал разноски платежей по строкам графика.

Неизменяемая запись: кто, сколько и на какую строку. Права на создание и
изменение не выданы никому — журнал пишет только код разноски. Отмена платежа
не стирает записи, а помечает их реверсированными: история должна остаться.

Существует потому, что Installment Schedule хранит ОДНУ ссылку на платёж. Строку,
закрытую двумя платежами, вторая ссылка затирала первую, и отмена второго платежа
обнуляла строку целиком — клиент терял деньги, внесённые первым.
"""

import frappe
from frappe.model.document import Document

STATUS_ACTIVE = "Активна"
STATUS_REVERSED = "Реверсирована"


class PaymentAllocation(Document):
    pass


def record(payment_transaction, installment_plan, rows, allocation_date=None):
    """Записать разноску платежа по строкам графика.

    `rows` — последовательность (имя строки Installment Schedule, сумма).
    Идемпотентно: если у платежа уже есть активные записи, ничего не пишем —
    повторная разноска не должна удваивать журнал.
    """
    from frappe.utils import flt, today

    if not payment_transaction or not rows:
        return 0

    if frappe.db.exists("Payment Allocation",
                        {"payment_transaction": payment_transaction, "status": STATUS_ACTIVE}):
        return 0

    stamp = allocation_date or today()
    written = 0
    for row_name, amount in rows:
        if not row_name or flt(amount) <= 0:
            continue
        doc = frappe.get_doc({
            "doctype": "Payment Allocation",
            "payment_transaction": payment_transaction,
            "installment_plan": installment_plan,
            "schedule_row": row_name,
            "allocated_amount": flt(amount),
            "allocation_date": stamp,
            "status": STATUS_ACTIVE,
        })
        doc.insert(ignore_permissions=True)
        written += 1
    return written


def active_for_payment(payment_transaction) -> list:
    """Активные записи разноски одного платежа."""
    return frappe.get_all(
        "Payment Allocation",
        filters={"payment_transaction": payment_transaction, "status": STATUS_ACTIVE},
        fields=["name", "installment_plan", "schedule_row", "allocated_amount"],
    )


def mark_reversed(allocation_names) -> None:
    """Пометить записи реверсированными. Не удалять: история должна остаться."""
    for name in allocation_names:
        frappe.db.set_value("Payment Allocation", name, "status", STATUS_REVERSED,
                            update_modified=False)
