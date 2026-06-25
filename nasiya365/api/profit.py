"""
Profit calculation engine for nasiya365.

Profit is recognised at the point of sale (accrual basis):
  - Goods margin = sale price - cost of goods (COGS), recognised when the device is sold.
  - Interest income = total_interest on installment plans. Recognition mode
    (at-sale vs at-collection) is controlled by Merchant Settings.interest_recognition.
  - Operating expenses come from the Expense doctype.

To avoid double-counting after the cash/installment decoupling:
  - Cash sale   = Sales Order (docstatus=1) NOT referenced by any Installment Plan.
  - Financed sale = Installment Plan (docstatus=1) not cancelled.

COGS for a sold device is the matching Stock Entry Item's (amount + expense),
located by IMEI. Falls back to 0 when no cost row is found (logged in the report).
"""

import frappe
from frappe.utils import flt, getdate

from nasiya365.api.bnpl_dashboard import _user_branch_clause

# Plans that represent a real, profit-bearing sale.
_LIVE_PLAN_STATUSES = ("Активный", "Просрочен", "Завершен")


def _settings():
    return frappe.get_single("Merchant Settings")


def _normalize_imei(imei):
    return (imei or "").strip().replace(" ", "").upper()


def _cogs_for_imei(imei):
    """Cost of goods for a single device, located by IMEI in Stock Entry Item."""
    s = _normalize_imei(imei)
    if not s:
        return 0.0
    tail6 = s[-6:] if len(s) >= 6 else s
    row = frappe.db.sql(
        """
        SELECT COALESCE(sei.amount, 0) + COALESCE(sei.expense, 0) AS cost
        FROM `tabStock Entry Item` sei
        WHERE REPLACE(UPPER(TRIM(sei.imei)), ' ', '') = %s
           OR RIGHT(REPLACE(UPPER(TRIM(sei.imei)), ' ', ''), 6) = %s
        LIMIT 1
        """,
        (s, tail6),
    )
    return flt(row[0][0]) if row else 0.0


def _branch_clause_for(alias):
    """Branch restriction reused from the dashboard (joins via sales_order.branch)."""
    return _user_branch_clause(alias)


def _explicit_branch_filter(branch):
    """Optional report-level branch filter (independent of the user's own scope)."""
    if not branch:
        return ("", [])
    return (
        " AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch = %s)",
        [branch],
    )


def compute_profit(from_date, to_date, branch=None):
    """
    Return a dict of profit components for the period [from_date, to_date].

    Keys: cash_revenue, cash_cogs, cash_margin,
          financed_revenue, financed_cogs, financed_margin,
          interest_income, gross_profit, expenses, net_profit, profit_basis.
    """
    from_date = getdate(from_date)
    to_date = getdate(to_date)
    settings = _settings()
    basis = settings.profit_basis or "Чистая прибыль"
    interest_mode = settings.interest_recognition or "При продаже"

    # ── Financed sales (Installment Plans) ──────────────────────────────────
    plan_branch_clause, plan_branch_params = _branch_clause_for("ip")
    expl_clause, expl_params = _explicit_branch_filter(branch)
    in_status = ",".join(["%s"] * len(_LIVE_PLAN_STATUSES))

    plans = frappe.db.sql(
        f"""
        SELECT ip.name, ip.imei, ip.principal_amount, ip.total_interest,
               ip.paid_amount, ip.financed_amount
        FROM `tabInstallment Plan` ip
        WHERE ip.docstatus = 1
          AND IFNULL(ip.status, '') IN ({in_status})
          AND IFNULL(ip.contract_status, '') != 'Отменен'
          AND DATE(ip.start_date) BETWEEN %s AND %s
          {plan_branch_clause}
          {expl_clause}
        """,
        (*_LIVE_PLAN_STATUSES, from_date, to_date, *plan_branch_params, *expl_params),
        as_dict=True,
    )

    financed_revenue = financed_cogs = interest_income = 0.0
    for p in plans:
        revenue = flt(p.principal_amount) or flt(p.financed_amount)
        financed_revenue += revenue
        financed_cogs += _cogs_for_imei(p.imei)
        if interest_mode == "При оплате":
            # Realised interest = portion of total_interest collected proportionally.
            total_due = flt(p.financed_amount) + flt(p.total_interest)
            if total_due > 0:
                interest_income += flt(p.total_interest) * (flt(p.paid_amount) / total_due)
        else:
            interest_income += flt(p.total_interest)

    financed_margin = financed_revenue - financed_cogs

    # ── Cash sales (Sales Orders not tied to a plan) ────────────────────────
    so_branch = " AND so.branch = %s" if branch else ""
    so_branch_params = [branch] if branch else []
    # User scope on Sales Order branch
    user_so_clause, user_so_params = _sales_order_user_clause("so")

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
          {user_so_clause}
        """,
        (from_date, to_date, *so_branch_params, *user_so_params),
        as_dict=True,
    )

    cash_revenue = cash_cogs = 0.0
    for so in cash_rows:
        cash_revenue += flt(so.total_amount)
        cash_cogs += _cogs_for_sales_order(so.name)
    cash_margin = cash_revenue - cash_cogs

    # ── Expenses ────────────────────────────────────────────────────────────
    exp_branch = " AND e.branch = %s" if branch else ""
    exp_branch_params = [branch] if branch else []
    user_exp_clause, user_exp_params = _expense_user_clause("e")
    expenses = flt(frappe.db.sql(
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
          {user_exp_clause}
        """,
        (from_date, to_date, *exp_branch_params, *user_exp_params),
    )[0][0])

    # ── Assemble by basis ───────────────────────────────────────────────────
    total_margin = cash_margin + financed_margin
    if basis == "Только маржа":
        gross_profit = total_margin
        interest_in_profit = 0.0
        expenses_in_profit = 0.0
    elif basis == "Валовая прибыль":
        gross_profit = total_margin + interest_income
        interest_in_profit = interest_income
        expenses_in_profit = 0.0
    else:  # Чистая прибыль
        gross_profit = total_margin + interest_income
        interest_in_profit = interest_income
        expenses_in_profit = expenses

    net_profit = gross_profit - expenses_in_profit

    return {
        "from_date": str(from_date),
        "to_date": str(to_date),
        "profit_basis": basis,
        "cash_revenue": cash_revenue,
        "cash_cogs": cash_cogs,
        "cash_margin": cash_margin,
        "financed_revenue": financed_revenue,
        "financed_cogs": financed_cogs,
        "financed_margin": financed_margin,
        "total_margin": total_margin,
        "interest_income": interest_income,
        "interest_in_profit": interest_in_profit,
        "expenses": expenses,
        "expenses_in_profit": expenses_in_profit,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
    }


def _cogs_for_sales_order(so_name):
    """Sum COGS across all items on a cash Sales Order, by each item's IMEI."""
    items = frappe.db.get_all(
        "Sales Order Item", filters={"parent": so_name}, fields=["imei"]
    )
    return sum(_cogs_for_imei(it.imei) for it in items)


def _sales_order_user_clause(alias):
    """Restrict Sales Orders to the caller's branches (None = unrestricted)."""
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    user = frappe.session.user
    if _is_unrestricted(user):
        return ("", [])
    branches = _get_user_branches(user)
    if not branches:
        return (" AND 1=0", [])
    ph = ",".join(["%s"] * len(branches))
    return (f" AND {alias}.branch IN ({ph})", list(branches))


def _expense_user_clause(alias):
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    user = frappe.session.user
    if _is_unrestricted(user):
        return ("", [])
    branches = _get_user_branches(user)
    if not branches:
        return (" AND 1=0", [])
    ph = ",".join(["%s"] * len(branches))
    return (f" AND {alias}.branch IN ({ph})", list(branches))


def compute_shareholder_split(profit_amount):
    """
    Split `profit_amount` among active shareholders per the configured model.
    Returns a list of {shareholder, basis, share_percent, amount}.
    """
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
    else:  # Фиксированный процент
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
    """Whitelisted wrapper used by reports and the dashboard."""
    return compute_profit(from_date, to_date, branch)


@frappe.whitelist()
def get_shareholder_distribution(from_date, to_date, branch=None):
    """Profit for the period plus its split among shareholders."""
    profit = compute_profit(from_date, to_date, branch)
    split = compute_shareholder_split(profit["net_profit"])
    return {"profit": profit, "distribution": split}
