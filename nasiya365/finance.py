"""Общие финансовые определения.

Модуль существует затем, чтобы одно и то же понятие не расходилось между
отчётом, дашбордом и задачами. Расхождение уже случалось: отчёт «Сборы и
просрочка» считал открытыми только ('Ожидает','Частично','Pending'), а ночная
задача переводит просроченные строки в 'Просрочен' — строка исчезала из отчёта
ровно в тот момент, когда становилась просроченной. Замер на стенде на одних
данных: дашборд 1 924,65, отчёт 0,00.
"""

# Статусы строки графика, при которых обязательство ещё не закрыто.
# 'Просрочен' входит: просроченная строка — это неоплаченная строка, а не
# отдельное состояние. Пустой статус тоже открыт — импорты его иногда не ставят.
UNSETTLED_SCHEDULE_STATUSES = ("Ожидает", "Частично", "Pending", "Просрочен")


def unsettled_schedule_predicate(alias: str, placeholders: str) -> str:
    """SQL-предикат «строка не закрыта» для указанного алиаса таблицы графика.

    `placeholders` — строка вида "%s,%s,%s,%s" под UNSETTLED_SCHEDULE_STATUSES.
    """
    return f"({alias}.status IN ({placeholders}) OR TRIM(IFNULL({alias}.status, '')) = '')"


def outstanding_principal(financed_amount, total_interest, paid_amount,
                          down_payment=0, has_down_payment_row=False) -> float:
    """Непогашенный основной долг по договору — кредитная база (решение владельца).

    Долгом считается только невозвращённая часть финансируемой суммы. Будущие
    проценты в неё не входят: иначе клиент со ставкой 2% в месяц на год «съедал»
    бы лимит на 24% больше, чем стоил купленный товар, а ставка — это цена
    услуги, а не выданные деньги.

    Платежи по flat-модели гасят основной долг и проценты пропорционально их
    долям в сумме к оплате, поэтому доля погашенного основного долга равна доле
    погашенной финансируемой части.

    Аванс вычитается ДО пропорции: он не входит в финансируемую сумму, а по FIFO
    гасится первым (строка 0 графика). Учитывается только у договоров, где эта
    строка есть, — у старых аванс не входит и в paid_amount.
    """
    from frappe.utils import flt

    financed = flt(financed_amount)
    financed_total = financed + flt(total_interest)
    if financed_total <= 0:
        return 0.0

    paid_to_financed = flt(paid_amount)
    if has_down_payment_row:
        paid_to_financed -= flt(down_payment)
    paid_to_financed = max(0.0, paid_to_financed)

    repaid_share = min(1.0, paid_to_financed / financed_total)
    return round(financed * (1.0 - repaid_share), 2)


# ——— Модель рассрочки ———————————————————————————————————————————————————————
#
# Модель — flat monthly: проценты начисляются на всю финансируемую сумму за весь
# срок и не уменьшаются по мере погашения (решение владельца, 2026-09-01).

# Средняя длина месяца: 365.25 / 12. Нужна, чтобы перевести недельный и
# двухнедельный график в месяцы — ставка задаётся в месяц.
DAYS_PER_MONTH = 30.44

# Версия формулы, записанная на договоре. Исторические договоры не
# пересчитываются: у каждого своя версия, и она решает, как его считать.
FORMULA_VERSION_LEGACY = 1    # срок = число платежей, независимо от частоты
FORMULA_VERSION_CALENDAR = 2  # срок = фактическая длина графика в месяцах


def schedule_term_months(number_of_installments, frequency) -> float:
    """Фактическая длина графика в месяцах.

    Раньше сроком считалось само число платежей. Для месячного графика это
    совпадает, а для недельного — нет: 12 платежей это 2,76 месяца, и процент
    брался за 12. Клиент переплачивал в 30.44/7 ≈ 4,35 раза.
    """
    from frappe.utils import cint

    n = cint(number_of_installments)
    if n <= 0:
        return 0.0

    freq = frequency or ""
    if "Еженедельно" in freq or "Weekly" in freq:
        return n * 7 / DAYS_PER_MONTH
    if "две недели" in freq or "Biweekly" in freq:
        return n * 14 / DAYS_PER_MONTH
    return float(n)


def flat_interest(financed_amount, monthly_rate_percent, term_months) -> float:
    """Проценты по flat-модели: на всю финансируемую сумму за весь срок."""
    from frappe.utils import flt

    return round(flt(financed_amount) * flt(monthly_rate_percent) / 100.0 * flt(term_months), 2)


def plan_interest(financed_amount, monthly_rate_percent, number_of_installments,
                  frequency, formula_version=FORMULA_VERSION_CALENDAR) -> float:
    """Проценты по договору с учётом версии его формулы.

    Единственная точка, где выбирается модель: договор, калькулятор и превью
    обязаны звать её, иначе продавец назовёт одну цифру, а договор напечатает
    другую — так и было, разница доходила до 22 раз.
    """
    from frappe.utils import cint

    if cint(formula_version) == FORMULA_VERSION_LEGACY:
        term = float(cint(number_of_installments))
    else:
        term = schedule_term_months(number_of_installments, frequency)
    return flat_interest(financed_amount, monthly_rate_percent, term)
