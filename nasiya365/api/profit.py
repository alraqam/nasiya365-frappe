"""
Profit calculation engine for nasiya365.

Two recognition methods (Merchant Settings.profit_method):

  • "По оплате (касса)"  — CASH BASIS (default):
      Profit is realised as money is actually collected during the period.
      Every Payment Transaction (status=Завершен) in [from, to] recognises the
      profit embedded in its deal, proportional to amount / total_contract_value.

  • "При продаже (начисление)" — ACCRUAL BASIS:
      The full margin (and interest) of a deal is recognised at the point of sale,
      regardless of how much has been collected.

Profit components:
  - Goods margin = sale price - cost of goods (COGS).
  - Interest income = total_interest on installment plans (cash sales carry none).
  - Operating expenses from the Expense doctype.

COGS for a sold device is the matching Stock Entry Item's (amount + expense),
located by IMEI. Falls back to 0 when no cost row is found.

Double-counting is avoided structurally:
  - Cash sale    = Sales Order (docstatus=1) NOT referenced by any Installment Plan.
  - Financed sale = Installment Plan (docstatus=1) not cancelled.
  - In cash basis, each Payment Transaction is counted exactly once via its reference.
"""

import frappe
from frappe.utils import flt, getdate

from nasiya365.api.bnpl_dashboard import _user_branch_clause
from nasiya365.api.recognition import recognized_delta, split_recognized

_LIVE_PLAN_STATUSES = ("Активный", "Просрочен", "Завершен")

# Static SQL fragment (no user input -- safe to interpolate via f-string) shared
# verbatim between _compute_cash and _compute_cost_recovery to resolve a
# Payment Transaction's branch from its reference (Installment Plan / Sales Order).
_PAYMENT_BRANCH_CASE = """CASE
                 WHEN pt.reference_doctype = 'Installment Plan' THEN (
                     SELECT COALESCE(NULLIF(ip.branch, ''), (
                         SELECT so.branch FROM `tabSales Order` so
                         WHERE so.name = ip.sales_order LIMIT 1))
                     FROM `tabInstallment Plan` ip
                     WHERE ip.name = pt.reference_name LIMIT 1)
                 WHEN pt.reference_doctype = 'Sales Order' THEN (
                     SELECT branch FROM `tabSales Order` WHERE name = pt.reference_name LIMIT 1)
               END"""


def _settings():
    return frappe.get_single("Merchant Settings")


def _normalize_imei(imei):
    return (imei or "").strip().replace(" ", "").upper()


def _unit_cost(row) -> float | None:
    """Себестоимость ЕДИНИЦЫ по строке поступления: (сумма + расходы) / количество.

    Раньше возвращалась стоимость всей строки. Для аппарата с IMEI количество
    равно единице и разницы не было, а для оптовой строки на 10 штук продажа
    одной списывала себестоимость всех десяти.
    """
    quantity = flt(row.get("quantity"))
    if quantity <= 0:
        return None
    return round((flt(row.get("amount")) + flt(row.get("expense"))) / quantity, 4)


def _cogs_for_imei(imei, as_of_date=None):
    """Себестоимость единицы товара, найденная по IMEI. None — если не найдена.

    Четыре ограничения, каждое из которых раньше отсутствовало:

    * только проведённые документы (`docstatus = 1`) — черновик не закупка;
    * только `entry_type = Поступление` — отпуск, перемещение и корректировка
      источником себестоимости не являются;
    * только ТОЧНОЕ совпадение IMEI. Поиск по последним шести цифрам оставался
      финансовым источником: два разных аппарата с одинаковым хвостом давали
      цену друг друга;
    * поступление не позже продажи. Прежний код при отсутствии подходящей даты
      брал самое свежее поступление вообще — то есть мог посчитать
      себестоимость по закупке, сделанной ПОЗЖЕ продажи.

    Если аппарат покупали несколько раз, берётся последняя закупка на дату
    продажи — тот цикл, в котором он и был продан.
    """
    s = _normalize_imei(imei)
    if not s:
        return None

    date_clause = "AND se.posting_date <= %s" if as_of_date else ""
    params = [s]
    if as_of_date:
        params.append(getdate(as_of_date))

    row = frappe.db.sql(
        f"""
        SELECT sei.quantity, sei.amount, sei.expense
        FROM `tabStock Entry Item` sei
        JOIN `tabStock Entry` se ON se.name = sei.parent
        WHERE se.docstatus = 1
          AND se.entry_type = 'Поступление'
          AND REPLACE(UPPER(TRIM(sei.imei)), ' ', '') = %s
          {date_clause}
        ORDER BY se.posting_date DESC, se.posting_time DESC, sei.idx DESC
        LIMIT 1
        """,
        params,
        as_dict=True,
    )
    return _unit_cost(row[0]) if row else None


def _cogs_from_stock_ref(ref, imei=None):
    """Себестоимость единицы по явной ссылке продажи на поступление.

    Ссылка привязывает продажу к конкретной партии и снимает неоднозначность,
    когда один IMEI закупали несколько раз. `ref` — имя строки Stock Entry Item
    (предпочтительно) либо самого Stock Entry.

    Возвращает None, если разрешить не удалось: вызывающий переходит к поиску
    по IMEI. Ограничения те же — проведённый документ и только поступление.
    """
    if not ref:
        return None

    row = frappe.db.sql(
        """
        SELECT sei.quantity, sei.amount, sei.expense
        FROM `tabStock Entry Item` sei
        JOIN `tabStock Entry` se ON se.name = sei.parent
        WHERE sei.name = %s
          AND se.docstatus = 1
          AND se.entry_type = 'Поступление'
        """,
        (ref,),
        as_dict=True,
    )
    if row:
        return _unit_cost(row[0])

    # Ссылка на само поступление: строку выбираем по точному IMEI, а если строка
    # в поступлении одна — берём её.
    items = frappe.db.sql(
        """
        SELECT sei.imei, sei.quantity, sei.amount, sei.expense
        FROM `tabStock Entry Item` sei
        JOIN `tabStock Entry` se ON se.name = sei.parent
        WHERE sei.parent = %s
          AND se.docstatus = 1
          AND se.entry_type = 'Поступление'
        ORDER BY sei.idx ASC
        """,
        (ref,),
        as_dict=True,
    )
    if not items:
        return None
    if imei:
        target = _normalize_imei(imei)
        for it in items:
            if _normalize_imei(it.get("imei")) == target:
                return _unit_cost(it)
    if len(items) == 1:
        return _unit_cost(items[0])
    return None


def _cogs_for_sale_item(imei, stock_ref=None, as_of_date=None, quantity=1):
    """Себестоимость проданного по одной строке. None — если определить нельзя.

    Порядок: точная ссылка на партию, затем поиск по IMEI на дату продажи.

    Ненайденная себестоимость больше не превращается в ноль: сделка без
    закупки выглядела стопроцентно маржинальной, и это молча завышало прибыль.
    Вызывающий решает, показать ошибку данных или пропустить строку.
    """
    unit = _cogs_from_stock_ref(stock_ref, imei)
    if unit is None:
        unit = _cogs_for_imei(imei, as_of_date)
    if unit is None:
        return None
    return round(unit * flt(quantity or 1), 2)


def _cogs_or_zero(cost, what) -> float:
    """Число для арифметики, когда себестоимость не определена.

    Ноль тут неизбежен — иначе отчёт не построится, — но он записывается в
    журнал ошибок. Молчаливый ноль означал бы стопроцентную маржу по сделке,
    у которой просто не нашлось закупки, и завышал бы прибыль.
    """
    if cost is None:
        frappe.log_error(title="COGS: себестоимость не определена", message=str(what))
        return 0.0
    return flt(cost)


def _cogs_for_sales_order(so_name, as_of_date=None, with_diagnostics=False):
    """
    Sum COGS across all items on a cash Sales Order.

    Количество умножается: раньше строка на пять аксессуаров стоила как один.

    Строки, себестоимость которых определить нельзя, в сумму не попадают и
    считаются отдельно — молча превращать их в ноль значит завышать прибыль.
    """
    so = frappe.db.get_value(
        "Sales Order", so_name, ["stock_entry", "order_date"], as_dict=True
    ) or frappe._dict()
    as_of = as_of_date or so.get("order_date")
    items = frappe.db.get_all(
        "Sales Order Item", filters={"parent": so_name},
        fields=["imei", "quantity"], order_by="idx asc",
    )
    single_ref = so.get("stock_entry") if len(items) == 1 else None

    total, unresolved = 0.0, 0
    for it in items:
        cost = _cogs_for_sale_item(it.imei, single_ref, as_of, quantity=it.quantity)
        if cost is None:
            unresolved += 1
            continue
        total += cost

    if unresolved:
        # Не бросаем: отчёт должен открыться. Но и не молчим — цифра занижена
        # ровно на непосчитанные строки, и это должно быть видно.
        frappe.log_error(
            title="COGS: себестоимость не определена",
            message=f"Заказ {so_name}: строк без закупки — {unresolved}",
        )

    total = round(total, 2)
    return (total, unresolved) if with_diagnostics else total


def _branch_clause_for(alias):
    """Branch restriction reused from the dashboard (plan's own branch, fallback to sales_order.branch)."""
    return _user_branch_clause(alias)


def _user_branches():
    """(is_unrestricted, set_of_branches). Empty set + restricted => sees nothing."""
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    user = frappe.session.user
    if _is_unrestricted(user):
        return (True, None)
    return (False, set(_get_user_branches(user) or []))


def _sales_order_user_clause(alias):
    """Restrict Sales Orders to the caller's branches (None = unrestricted)."""
    unrestricted, branches = _user_branches()
    if unrestricted:
        return ("", [])
    if not branches:
        return (" AND 1=0", [])
    ph = ",".join(["%s"] * len(branches))
    return (f" AND {alias}.branch IN ({ph})", list(branches))


def _expense_user_clause(alias):
    return _sales_order_user_clause(alias)


# ── Per-deal embedded profit rates ──────────────────────────────────────────

def _plan_profit(plan):
    """
    Embedded profit for an installment plan.
    Returns (embedded_margin, total_interest, denominator).
    denominator = total contract value the customer pays (principal + interest).
    """
    cogs = _cogs_or_zero(
        _cogs_for_sale_item(plan.imei, plan.get("stock_entry"), plan.get("start_date")),
        f"План {plan.get('name')}, IMEI {plan.get('imei')}",
    )
    revenue = flt(plan.principal_amount) or flt(plan.financed_amount)
    embedded_margin = revenue - cogs
    interest = flt(plan.total_interest)
    denom = flt(plan.total_amount) or (flt(plan.financed_amount) + interest) or revenue
    return embedded_margin, interest, denom


def _so_profit(so):
    """Embedded profit for a cash Sales Order. Returns (margin, denominator)."""
    cogs = _cogs_for_sales_order(so.name)
    revenue = flt(so.total_amount)
    return revenue - cogs, (revenue or 1.0)


# ── Main entry ──────────────────────────────────────────────────────────────

def compute_profit(from_date, to_date, branch=None):
    from_date = getdate(from_date)
    to_date = getdate(to_date)
    settings = _settings()
    method = settings.profit_method or "По оплате (касса)"

    if method.startswith("Возмещение"):
        comp = _compute_cost_recovery(from_date, to_date, branch)
    elif method.startswith("При продаже"):
        comp = _compute_accrual(from_date, to_date, branch)
    else:
        comp = _compute_cash(from_date, to_date, branch)

    comp["expenses"] = _period_expenses(from_date, to_date, branch)
    return _apply_basis(comp, settings, method)


def _apply_basis(comp, settings, method):
    """Fold margin/interest/expenses into gross & net profit per profit_basis."""
    basis = settings.profit_basis or "Чистая прибыль"
    total_margin = comp["cash_margin"] + comp["financed_margin"]
    interest = comp["interest_income"]
    expenses = comp["expenses"]

    if basis == "Только маржа":
        gross = total_margin
        interest_in = 0.0
        expenses_in = 0.0
    elif basis == "Валовая прибыль":
        gross = total_margin + interest
        interest_in = interest
        expenses_in = 0.0
    else:  # Чистая прибыль
        gross = total_margin + interest
        interest_in = interest
        expenses_in = expenses

    comp.update({
        "profit_basis": basis,
        "profit_method": method,
        "total_margin": total_margin,
        "interest_in_profit": interest_in,
        "expenses_in_profit": expenses_in,
        "gross_profit": gross,
        "net_profit": gross - expenses_in,
    })
    return comp


def _period_expenses(from_date, to_date, branch):
    exp_branch = " AND e.branch = %s" if branch else ""
    exp_branch_params = [branch] if branch else []
    user_clause, user_params = _expense_user_clause("e")
    return flt(frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(
            CASE WHEN e.currency = 'UZS' AND e.exchange_rate > 0
                 THEN e.amount / e.exchange_rate ELSE e.amount END
        ), 0)
        FROM `tabExpense` e
        WHERE e.docstatus < 2
          AND IFNULL(e.status, '') != 'Отменен'
          AND DATE(e.expense_date) BETWEEN %s AND %s
          {exp_branch}
          {user_clause}
        """,
        (from_date, to_date, *exp_branch_params, *user_params),
    )[0][0])


# ── CASH BASIS ──────────────────────────────────────────────────────────────

def _compute_cash(from_date, to_date, branch):
    """
    Recognise profit from payments collected in the period, proportional to each
    deal's embedded profit. Each Payment Transaction is counted once.
    """
    unrestricted, user_branches = _user_branches()

    payments = frappe.db.sql(
        f"""
        SELECT pt.reference_doctype AS rdt,
               pt.reference_name   AS rn,
               pt.amount           AS amount,
               {_PAYMENT_BRANCH_CASE} AS branch
        FROM `tabPayment Transaction` pt
        WHERE pt.docstatus < 2
          AND pt.status = 'Завершен'
          AND DATE(pt.payment_date) BETWEEN %s AND %s
          AND pt.reference_name IS NOT NULL
        """,
        (from_date, to_date),
        as_dict=True,
    )

    cash_margin = financed_margin = interest_income = 0.0
    cash_revenue = financed_revenue = 0.0
    plan_cache, so_cache = {}, {}

    for pay in payments:
        b = pay.branch
        if branch and b != branch:
            continue
        if not unrestricted and (b not in user_branches):
            continue
        amount = flt(pay.amount)
        if amount <= 0:
            continue

        if pay.rdt == "Installment Plan":
            plan = plan_cache.get(pay.rn)
            if plan is None:
                plan = frappe.db.get_value(
                    "Installment Plan", pay.rn,
                    ["imei", "principal_amount", "financed_amount", "total_interest",
                     "total_amount", "status", "contract_status",
                     "stock_entry", "start_date"],
                    as_dict=True,
                )
                plan_cache[pay.rn] = plan
            if not plan or plan.contract_status == "Отменен" or plan.status == "Списан":
                continue
            embedded_margin, interest, denom = _plan_profit(plan)
            if denom <= 0:
                continue
            frac = amount / denom
            financed_margin += embedded_margin * frac
            interest_income += interest * frac
            financed_revenue += amount

        elif pay.rdt == "Sales Order":
            so = so_cache.get(pay.rn)
            if so is None:
                so = frappe.db.get_value(
                    "Sales Order", pay.rn, ["name", "total_amount", "docstatus"], as_dict=True
                )
                so_cache[pay.rn] = so
            if not so or so.docstatus == 2:
                continue
            margin, denom = _so_profit(so)
            frac = amount / denom if denom else 0
            cash_margin += margin * frac
            cash_revenue += amount

    return {
        "from_date": str(from_date),
        "to_date": str(to_date),
        "cash_revenue": cash_revenue,
        "cash_cogs": cash_revenue - cash_margin,
        "cash_margin": cash_margin,
        "financed_revenue": financed_revenue,
        "financed_cogs": financed_revenue - financed_margin - interest_income,
        "financed_margin": financed_margin,
        "interest_income": interest_income,
    }


# ── ACCRUAL BASIS ───────────────────────────────────────────────────────────

def _compute_accrual(from_date, to_date, branch):
    """Recognise the full margin + interest of each deal at point of sale."""
    plan_branch_clause, plan_branch_params = _branch_clause_for("ip")
    expl = (
        " AND (ip.branch = %s OR ((ip.branch IS NULL OR ip.branch = '')"
        " AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch = %s)))"
    ) if branch else ""
    expl_params = [branch, branch] if branch else []
    in_status = ",".join(["%s"] * len(_LIVE_PLAN_STATUSES))

    plans = frappe.db.sql(
        f"""
        SELECT ip.name, ip.imei, ip.principal_amount, ip.total_interest,
               ip.financed_amount, ip.total_amount, ip.down_payment,
               ip.stock_entry, ip.start_date
        FROM `tabInstallment Plan` ip
        WHERE ip.docstatus = 1
          AND IFNULL(ip.status, '') IN ({in_status})
          AND IFNULL(ip.contract_status, '') != 'Отменен'
          AND DATE(ip.start_date) BETWEEN %s AND %s
          {plan_branch_clause}
          {expl}
        """,
        (*_LIVE_PLAN_STATUSES, from_date, to_date, *plan_branch_params, *expl_params),
        as_dict=True,
    )
    financed_revenue = financed_cogs = interest_income = down_payment_total = 0.0
    for p in plans:
        revenue = flt(p.principal_amount) or flt(p.financed_amount)
        financed_revenue += revenue
        financed_cogs += _cogs_or_zero(
            _cogs_for_sale_item(p.imei, p.get("stock_entry"), p.get("start_date")),
            f"План {p.get('name')}, IMEI {p.get('imei')}",
        )
        interest_income += flt(p.total_interest)
        down_payment_total += flt(p.down_payment)
    financed_margin = financed_revenue - financed_cogs

    so_branch = " AND so.branch = %s" if branch else ""
    so_branch_params = [branch] if branch else []
    user_clause, user_params = _sales_order_user_clause("so")
    cash_rows = frappe.db.sql(
        f"""
        SELECT so.name, so.total_amount
        FROM `tabSales Order` so
        WHERE so.docstatus = 1
          AND DATE(so.order_date) BETWEEN %s AND %s
          AND NOT EXISTS (
              SELECT 1 FROM `tabInstallment Plan` ip2
              WHERE ip2.sales_order = so.name AND ip2.docstatus < 2
          )
          {so_branch}
          {user_clause}
        """,
        (from_date, to_date, *so_branch_params, *user_params),
        as_dict=True,
    )
    cash_revenue = cash_cogs = 0.0
    for so in cash_rows:
        cash_revenue += flt(so.total_amount)
        cash_cogs += _cogs_for_sales_order(so.name)
    cash_margin = cash_revenue - cash_cogs

    return {
        "from_date": str(from_date),
        "to_date": str(to_date),
        "cash_revenue": cash_revenue,
        "cash_cogs": cash_cogs,
        "cash_margin": cash_margin,
        "financed_revenue": financed_revenue,
        "financed_cogs": financed_cogs,
        "financed_margin": financed_margin,
        "interest_income": interest_income,
        "down_payment": down_payment_total,
    }


# ── COST RECOVERY BASIS ──────────────────────────────────────────────────────

def _plan_cogs(plan):
    return _cogs_or_zero(
        _cogs_for_sale_item(plan.imei, plan.get("stock_entry"), plan.get("start_date")),
        f"План {plan.get('name')}, IMEI {plan.get('imei')}",
    )


def _collected_before(rdt, rn, from_date):
    """Sum of completed payments on a deal strictly before the period start."""
    return flt(frappe.db.sql(
        """SELECT COALESCE(SUM(amount), 0) FROM `tabPayment Transaction`
           WHERE docstatus < 2 AND status = 'Завершен'
             AND reference_doctype = %s AND reference_name = %s
             AND DATE(payment_date) < %s""",
        (rdt, rn, from_date))[0][0])


def _compute_cost_recovery(from_date, to_date, branch):
    """Cost-recovery recognition. Раздел 1 (sales/potential) reuses accrual;
    Раздел 2 (recognized) is driven by cumulative collections vs COGS per deal."""
    unrestricted, user_branches = _user_branches()

    # ── Раздел 1: sales made in the period (potential) — reuse accrual as-is.
    sales = _compute_accrual(from_date, to_date, branch)

    # ── Раздел 2: recognition from collections.
    # Deals with >= 1 completed payment inside the window, with each deal's
    # branch resolved the same way as _compute_cash.
    # NOTE: unlike _compute_cash (which skips amount <= 0), cost recovery lets
    # negative payments (refunds) flow through: a refund correctly REVERSES prior
    # recognition via a negative recognized_delta. Intentional divergence.
    window = frappe.db.sql(
        f"""
        SELECT pt.reference_doctype AS rdt, pt.reference_name AS rn,
               SUM(pt.amount) AS win,
               {_PAYMENT_BRANCH_CASE} AS branch
        FROM `tabPayment Transaction` pt
        WHERE pt.docstatus < 2 AND pt.status = 'Завершен'
          AND pt.reference_name IS NOT NULL
          AND DATE(pt.payment_date) BETWEEN %s AND %s
        GROUP BY pt.reference_doctype, pt.reference_name
        """,
        (from_date, to_date), as_dict=True,
    )

    rec_margin_cash = rec_margin_fin = rec_interest = 0.0
    collected_total = 0.0
    plan_cache, so_cache = {}, {}

    for w in window:
        b = w.branch
        if branch and b != branch:
            continue
        if not unrestricted and (b not in user_branches):
            continue

        before = _collected_before(w.rdt, w.rn, from_date)
        after = before + flt(w.win)

        if w.rdt == "Installment Plan":
            plan = plan_cache.get(w.rn)
            if plan is None:
                plan = frappe.db.get_value(
                    "Installment Plan", w.rn,
                    ["imei", "principal_amount", "financed_amount", "total_interest",
                     "total_amount", "contract_status", "status", "stock_entry", "start_date"],
                    as_dict=True)
                plan_cache[w.rn] = plan
            if not plan or plan.contract_status == "Отменен" or plan.status == "Списан":
                continue
            cogs = _plan_cogs(plan)
            margin = (flt(plan.principal_amount) or flt(plan.financed_amount)) - cogs
            interest = flt(plan.total_interest)
            total_profit = margin + interest
            delta = recognized_delta(before, after, cogs, total_profit)
            m_part, i_part = split_recognized(delta, margin, total_profit)
            rec_margin_fin += m_part
            rec_interest += i_part
            collected_total += flt(w.win)

        elif w.rdt == "Sales Order":
            so = so_cache.get(w.rn)
            if so is None:
                so = frappe.db.get_value(
                    "Sales Order", w.rn, ["name", "total_amount", "docstatus"], as_dict=True)
                so_cache[w.rn] = so
            if not so or so.docstatus == 2:
                continue
            cogs = _cogs_for_sales_order(so.name)
            margin = flt(so.total_amount) - cogs
            total_profit = margin  # cash sale: no interest
            delta = recognized_delta(before, after, cogs, total_profit)
            m_part, _ = split_recognized(delta, margin, total_profit)
            rec_margin_cash += m_part
            collected_total += flt(w.win)

    # potential (Раздел 1) totals derived from accrual
    sales_total_margin = flt(sales["cash_margin"]) + flt(sales["financed_margin"])
    sales_interest = flt(sales["interest_income"])

    comp = dict(sales)  # carry cash/financed revenue+cogs (sales side)
    comp.update({
        "from_date": str(from_date),
        "to_date": str(to_date),
        # Раздел 1 (potential)
        "sales_cash_revenue": flt(sales["cash_revenue"]),
        "sales_cash_cogs": flt(sales["cash_cogs"]),
        "sales_cash_margin": flt(sales["cash_margin"]),
        "sales_financed_revenue": flt(sales["financed_revenue"]),
        "sales_financed_cogs": flt(sales["financed_cogs"]),
        "sales_financed_margin": flt(sales["financed_margin"]),
        "sales_total_margin": sales_total_margin,
        "sales_interest": sales_interest,
        "sales_down_payment": flt(sales.get("down_payment")),
        "potential_profit": sales_total_margin + sales_interest,
        # Раздел 2 (recognized) — these DRIVE _apply_basis / net_profit
        "cash_margin": rec_margin_cash,
        "financed_margin": rec_margin_fin,
        "interest_income": rec_interest,
        "collected": collected_total,
    })
    return comp


# ── Shareholders ────────────────────────────────────────────────────────────

def compute_shareholder_split(profit_amount):
    """Split `profit_amount` among active shareholders per the configured model."""
    settings = _settings()
    model = settings.shareholder_split_model or "Фиксированный процент"
    active = [s for s in (settings.shareholders or []) if s.is_active]
    if not active:
        return []

    result = []
    if model == "Пропорционально капиталу":
        total_capital = sum(flt(s.capital_contributed) for s in active)
        for s in active:
            pct = (flt(s.capital_contributed) / total_capital * 100) if total_capital else 0
            result.append({
                "shareholder": s.shareholder_name,
                "basis": flt(s.capital_contributed),
                "share_percent": round(pct, 2),
                "amount": round(flt(profit_amount) * pct / 100, 2),
            })
    else:
        for s in active:
            pct = flt(s.share_percent)
            result.append({
                "shareholder": s.shareholder_name,
                "basis": pct,
                "share_percent": pct,
                "amount": round(flt(profit_amount) * pct / 100, 2),
            })
    return result


@frappe.whitelist()
def get_profit_summary(from_date, to_date, branch=None):
    return compute_profit(from_date, to_date, branch)


@frappe.whitelist()
def get_shareholder_distribution(from_date, to_date, branch=None):
    profit = compute_profit(from_date, to_date, branch)
    split = compute_shareholder_split(profit["net_profit"])
    return {"profit": profit, "distribution": split}
