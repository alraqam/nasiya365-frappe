import frappe


def execute():
    """Пересчитать долг и лимит клиентов под новую кредитную базу.

    Кредитной базой стал непогашенный основной долг без будущих процентов
    (решение владельца, 2026-09-01) — раньше долгом считался весь остаток
    договора вместе с процентами.

    total_debt и available_limit у Customer Profile — производные поля: они
    пересчитываются только когда клиента что-нибудь тронет. Без этого патча на
    сайте одновременно жили бы две базы: у тронутых клиентов новая, у остальных
    старая, и разницу нельзя было бы объяснить, глядя на карточку.

    Исторические финансовые документы патч НЕ трогает: договоры, платежи и
    графики остаются как есть, пересчитываются только производные показатели.
    Откат: вернуть прежний код и выполнить тот же пересчёт — он идемпотентен.
    """
    customers = frappe.get_all("Customer Profile", pluck="name")
    frappe.logger().info(f"recompute_customer_credit_base: {len(customers)} клиентов")

    for name in customers:
        try:
            frappe.get_doc("Customer Profile", name).update_statistics()
        except Exception:
            # Один битый профиль не должен останавливать миграцию всего сайта.
            frappe.log_error(
                title="recompute_customer_credit_base",
                message=f"Клиент {name}: {frappe.get_traceback()}",
            )

    frappe.db.commit()
