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

_LIVE_PLAN_STATUSES = ("Активный", "Просрочен", "Завершен")


def _settings():
    return frappe.get_single("Merchant Settings")


def _normalize_imei(imei):
    return (imei or "").strip().replace(" ", "").upper()


def _cogs_for_imei(imei, as_of_date=None):
    """
    Cost of goods for a single device, located by IMEI in Stock Entry Item.

    When the same IMEI was purchased more than once (device bought -> sold ->
    bought again), the two cycles must NOT collapse. So:
      - prefer an exact full-IMEI match over the fuzzy last-6-digits match;
      - among matches, pick the most recent purchase on/before the sale date
        (`as_of_date`) -- the lot the device was actually bought into for THAT
        sale -- instead of an arbitrary `LIMIT 1` row.
    Falls back to the most recent purchase overall when no dated match exists.
    """
    s = _normalize_imei(imei)
    if not s:
        return 0.0
    tail6 = s[-6:] if len(s) >= 6 else s

    def _lookup(with_date):
        date_clause = "AND se.posting_date <= %s" if with_date else ""
        params = [s, s, tail6]
        if with_date:
            params.append(with_date)
        return frappe.db.sql(
            f"""
            SELECT COALESCE(sei.amount, 0) + COALESCE(sei.expense, 0) AS cost,
                   CASE WHEN REPLACE(UPPER(TRIM(sei.imei)), ' ', '') = %s
                        THEN 0 ELSE 1 END AS match_rank
            FROM `tabStock Entry Item` sei
            JOIN `tabStock Entry` se ON se.name = sei.parent
            WHERE se.docstatus < 2
              AND (
                    REPLACE(UPPER(TRIM(sei.imei)), ' ', '') = %s
                    OR RIGHT(REPLACE(UPPER(TRIM(sei.imei)), ' ', ''), 6) = %s
                  )
              {date_clause}
            ORDER BY match_rank ASC, se.posting_date DESC, se.posting_time DESC, sei.idx DESC
            LIMIT 1
            """,
            params,
        )

    row = _lookup(getdate(as_of_date)) if as_of_date else _lookup(None)
    if not row and as_of_date:
        # No purchase on/before the sale date -> fall back to the latest overall.
        row = _lookup(None)
    return flt(row[0][0]) if row else 0.0


def _cogs_from_stock_ref(ref, imei=None):
    """
    Precise COGS from an explicit purchase reference (the sale's `stock_entry`),
    which pins the sale to the exact lot it was sold from -- unambiguous even
    when the same IMEI was bought several times. Returns None when it cannot
    resolve, so callers fall back to the IMEI lookup.

    `ref` may be a Stock Entry Item row name (preferred) or a parent Stock Entry.
    """
    if not ref:
        return None
    if frappe.db.exists("Stock Entry Item", ref):
        r = frappe.db.get_value(
            "Stock Entry Item", ref, ["amount", "expense"], as_dict=True
        )
        if r:
            return flt(r.amount) + flt(r.expense)
    if frappe.db.exists("Stock Entry", ref):
        items = frappe.db.get_all(
            "Stock Entry Item", filters={"parent": ref},
            fields=["imei", "amount", "expense"], order_by="idx asc",
        )
        if items:
            if imei:
                s = _normalize_imei(imei)
                tail6 = s[-6:] if len(s) >= 6 else s
                for it in items:
                    n = _normalize_imei(it.imei)
                    if n and (n == s or (len(n) >= 6 and n[-6:] == tail6)):
                        return flt(it.amount) + flt(it.expense)
            if len(items) == 1:
                return flt(items[0].amount) + flt(items[0].expense)
    return None


def _cogs_for_sale_item(imei, stock_ref=None, as_of_date=None):
    """
    COGS for one sold unit -- the single entry point used across the engine.
      A) exact purchase via the sale's `stock_entry` link, when present;
      B) otherwise a date-aware IMEI match (nearest purchase on/before the sale).
    """
    cost = _cogs_from_stock_ref(stock_ref, imei)
    if cost is not None:
        return cost
    return _cogs_for_imei(imei, as_of_date)


def _cogs_for_sales_order(so_name, as_of_date=None):
    """
    Sum COGS across all items on a cash Sales Order.

    Uses the order's own `stock_entry` link for single-line orders (exact lot),
    and a date-aware IMEI match otherwise, so repeated purchases of the same
    IMEI resolve to the correct buy/sell cycle.
    """
    so = frappe.db.get_value(
        "Sales Order", so_name, ["stock_entry", "order_date"], as_dict=True
    ) or frappe._dict()
    as_of = as_of_date or so.get("order_date")
    items = frappe.db.get_all(
        "Sales Order Item", filters={"parent": so_name}, fields=["imei"]
    )
    single_ref = so.get("stock_entry") if len(items) == 1 else None
    return sum(_cogs_for_sale_item(it.imei, single_ref, as_of) for it in items)


def _branch_clause_for(alias):
    """Branch restriction reused from the dashboard (joins via sales_order.branch)."""
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
    cogs = _cogs_for_sale_item(plan.imei, plan.get("stock_entry"), plan.get("start_date"))
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

    if method.startswith("При продаже"):
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
        """
        SELECT pt.reference_doctype AS rdt,
               pt.reference_name   AS rn,
               pt.amount           AS amount,
               CASE
                 WHEN pt.reference_doctype = 'Installment Plan' THEN (
                     SELECT so.branch FROM `tabSales Order` so
                     JOIN `tabInstallment Plan` ip ON ip.sales_order = so.name
                     WHERE ip.name = pt.reference_name LIMIT 1)
                 WHEN pt.reference_doctype = 'Sales Order' THEN (
                     SELECT branch FROM `tabSales Order` WHERE name = pt.reference_name LIMIT 1)
               END AS branch
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
            if not plan or (plan.contract_status == "Отменен"):
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
                    "Sales Order", pay.rn, ["name", "total_amount"], as_dict=True
                )
                so_cache[pay.rn] = so
            if not so:
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
    expl = " AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch = %s)" if branch else ""
    expl_params = [branch] if branch else []
    in_status = ",".join(["%s"] * len(_LIVE_PLAN_STATUSES))

    plans = frappe.db.sql(
        f"""
        SELECT ip.name, ip.imei, ip.principal_amount, ip.total_interest,
               ip.financed_amount, ip.total_amount, ip.stock_entry, ip.start_date
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
    financed_revenue = financed_cogs = interest_income = 0.0
    for p in plans:
        revenue = flt(p.principal_amount) or flt(p.financed_amount)
        financed_revenue += revenue
        financed_cogs += _cogs_for_sale_item(p.imei, p.get("stock_entry"), p.get("start_date"))
        interest_income += flt(p.total_interest)
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
    }


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
