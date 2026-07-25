import json
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, get_last_day, getdate, nowdate, today


def _require_doc_permission(doctype: str, name: str, ptype: str = "read") -> None:
    """Raise PermissionError if caller cannot access `name` of `doctype`."""
    if not name:
        frappe.throw(_("{0} is required").format(doctype))
    if not frappe.db.exists(doctype, name):
        frappe.throw(_("{0} not found").format(doctype))
    if not frappe.has_permission(doctype, ptype=ptype, doc=name):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def _user_branch_clause(plan_alias: str = "ip"):
    """
    Return (sql_fragment, params) restricting Installment-Plan-backed queries
    to the caller's assigned branches. Fragment begins with ' AND ' when non-empty.
    For unrestricted users returns ('', ()).
    For users with zero branches returns a no-match fragment so they see nothing.
    """
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    user = frappe.session.user
    if _is_unrestricted(user):
        return ("", ())
    branches = _get_user_branches(user)
    if not branches:
        return (" AND 1=0", ())
    placeholders = ",".join(["%s"] * len(branches))
    fragment = (
        f" AND {plan_alias}.sales_order IN "
        f"(SELECT name FROM `tabSales Order` WHERE branch IN ({placeholders}))"
    )
    return (fragment, tuple(branches))


def _user_branch_list():
    """Return (is_unrestricted, branches list). Empty list means user sees nothing."""
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    user = frappe.session.user
    if _is_unrestricted(user):
        return (True, [])
    return (False, _get_user_branches(user))

# Поступление already consumed (cash/BNPL issue) or returned — hide from new installment plans.
_STOCK_ENTRY_UNAVAILABLE_BUSINESS = ("Наличные", "Рассрочка", "Возврат")

# Installment Schedule child default was "Pending" while valid options are Russian — include both in SQL.
_OPEN_SCHEDULE_STATUSES = ("Ожидает", "Частично", "Pending")

# Collector lists: not only submitted (1); drafts often stay docstatus 0 until first payment flow.
_COLLECTION_PLAN_WHERE = (
    "ip.docstatus < 2"
    " AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан', 'Отменен')"
    " AND IFNULL(ip.contract_status, '') != 'Отменен'"
)


def _collection_customer_select():
    """Prefer Customer Profile name; fall back to customer id."""
    return (
        "COALESCE(NULLIF(TRIM(cp.full_name), ''), ip.customer) "
        "AS customer_name"
    )


def _schedule_open_predicate(in_open_placeholders: str) -> str:
    """Treat blank/null schedule status as unpaid/open (imports sometimes omit it)."""
    return (
        f"(isc.status IN ({in_open_placeholders}) OR TRIM(IFNULL(isc.status, '')) = '')"
    )


def _to_float(value):
    return flt(value or 0)


def _safe_limit(value, default_value=10, max_value=100):
    limit = cint(value or default_value)
    if limit <= 0:
        return default_value
    return min(limit, max_value)


_IMEI_MIN_DIGITS = 3


def _sanitize_imei_term(imei):
    """Digits-only IMEI search term, or None when too short to search.

    Cashiers paste spaces/labels and only remember a tail of the IMEI, so we
    keep just the digits. Terms under 3 digits are refused (too broad). Because
    the result is digits-only, it can never contain LIKE wildcards.
    """
    digits = re.sub(r"\D", "", imei or "")
    if len(digits) < _IMEI_MIN_DIGITS:
        return None
    return digits


def _risk_level_from_score(score):
    score = cint(score or 0)
    if score >= 70:
        return "Низкий"
    if score >= 40:
        return "Средний"
    return "Высокий"


def _trend(current, previous):
    """Return {pct, direction} for KPI deltas; None if not meaningful."""
    current = _to_float(current)
    previous = _to_float(previous)
    if previous == 0 and current == 0:
        return None
    if previous == 0:
        return {"pct": 100.0, "direction": "up"}
    delta = current - previous
    pct = round((delta / abs(previous)) * 100, 1)
    direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
    return {"pct": abs(pct), "direction": direction}


def _revenue_mtd_window(as_of):
    """(start_date, end_date) for current month MTD ending on as_of."""
    start = get_first_day(as_of)
    return start, as_of


def _revenue_mtd_prev_month_window(as_of):
    """Comparable MTD window in the previous calendar month."""
    first_this = get_first_day(as_of)
    last_prev_month = add_days(first_this, -1)
    prev_start = get_first_day(last_prev_month)
    day = as_of.day
    last_day = get_last_day(prev_start).day
    end_day = min(day, last_day)
    prev_end = add_days(prev_start, end_day - 1)
    return prev_start, prev_end


def _sum_payments_between(start, end):
    plan_branch_clause, branch_params = _user_branch_clause("ip")
    # Payment Transaction has no branch itself; filter via its linked Installment Plan.
    pt_filter = (
        "AND pt.reference_doctype = 'Installment Plan' "
        "AND EXISTS (SELECT 1 FROM `tabInstallment Plan` ip "
        f"           WHERE ip.name = pt.reference_name {plan_branch_clause})"
    ) if plan_branch_clause else ""
    return _to_float(
        frappe.db.sql(
            f"""
            SELECT COALESCE(SUM(pt.amount), 0)
            FROM `tabPayment Transaction` pt
            WHERE pt.docstatus < 2
              AND pt.status = 'Завершен'
              AND pt.payment_date BETWEEN %s AND %s
              {pt_filter}
            """,
            (start, end, *branch_params),
        )[0][0]
    )


def _kpi_metrics(base_date, branch=None):
    plan_clause, branch_params = _user_branch_clause("ip")

    outstanding = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(ip.remaining_balance), 0)
        FROM `tabInstallment Plan` ip
        WHERE ip.docstatus < 2
          AND ip.status IN ('Активный', 'Просрочен')
          {plan_clause}
    """,
        branch_params,
    )[0][0]

    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
    open_pred = _schedule_open_predicate(in_open)
    due_today = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND {open_pred}
          AND isc.due_date = %s
          {plan_clause}
    """,
        (*_OPEN_SCHEDULE_STATUSES, base_date, *branch_params),
    )[0][0]

    overdue = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          {plan_clause}
    """,
        (base_date, *_OPEN_SCHEDULE_STATUSES, *branch_params),
    )[0][0]

    pt_filter = (
        "AND pt.reference_doctype = 'Installment Plan' "
        "AND EXISTS (SELECT 1 FROM `tabInstallment Plan` ip "
        f"           WHERE ip.name = pt.reference_name {plan_clause})"
    ) if plan_clause else ""
    collected_today = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(pt.amount), 0)
        FROM `tabPayment Transaction` pt
        WHERE pt.docstatus < 2
          AND pt.status = 'Завершен'
          AND pt.payment_date = %s
          {pt_filter}
    """,
        (base_date, *branch_params),
    )[0][0]

    active_contracts_sql = f"""
        SELECT COUNT(*)
        FROM `tabInstallment Plan` ip
        WHERE ip.docstatus < 2
          AND ip.status IN ('Активный', 'Просрочен')
          {plan_clause}
    """
    active_contracts = frappe.db.sql(active_contracts_sql, branch_params)[0][0]

    mtd_start, mtd_end = _revenue_mtd_window(base_date)
    revenue_mtd = _sum_payments_between(mtd_start, mtd_end)

    return {
        "outstanding_amount": _to_float(outstanding),
        "due_today_amount": _to_float(due_today),
        "overdue_amount": _to_float(overdue),
        "cash_collected_today": _to_float(collected_today),
        "active_contracts": cint(active_contracts or 0),
        "revenue_mtd": _to_float(revenue_mtd),
        "filters": {"date": str(base_date), "branch": branch},
    }


@frappe.whitelist()
def get_bnpl_kpis(date=None, branch=None):
    base_date = getdate(date) if date else getdate(today())
    data = _kpi_metrics(base_date, branch=branch)
    return {
        "outstanding_amount": data["outstanding_amount"],
        "due_today_amount": data["due_today_amount"],
        "overdue_amount": data["overdue_amount"],
        "cash_collected_today": data["cash_collected_today"],
        "active_contracts": data["active_contracts"],
        "revenue_mtd": data["revenue_mtd"],
        "filters": data["filters"],
    }


@frappe.whitelist()
def log_collection_call(installment_plan, outcome, notes="", promised_date=None, next_call_date=None):
    """Save a Collection Log entry for a collector call outcome."""
    _require_doc_permission("Installment Plan", installment_plan, "write")
    customer = frappe.db.get_value("Installment Plan", installment_plan, "customer")
    if not customer:
        frappe.throw(_("Installment Plan not found"))

    doc = frappe.new_doc("Collection Log")
    doc.customer = customer
    doc.installment_plan = installment_plan
    doc.outcome = outcome
    doc.notes = notes or ""
    doc.promised_date = promised_date or None
    doc.next_call_date = next_call_date or None
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _last_call_subquery():
    """SQL fragment to LEFT JOIN the most recent Collection Log per installment plan."""
    return """
        LEFT JOIN `tabCollection Log` cl
            ON cl.name = (
                SELECT name FROM `tabCollection Log`
                WHERE installment_plan = ip.name
                ORDER BY call_datetime DESC
                LIMIT 1
            )
    """


@frappe.whitelist()
def get_overdue_list(limit=20, branch=None, collector=None):
    query_limit = _safe_limit(limit, default_value=20, max_value=200)
    base_date = getdate(today())
    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
    open_pred = _schedule_open_predicate(in_open)
    # DATEDIFF (1st), subquery due_date <= (2nd), overdue due_date < (3rd), open statuses
    args = [base_date, base_date, base_date, *_OPEN_SCHEDULE_STATUSES]
    collector_clause = ""
    if collector:
        collector_clause = """
          AND EXISTS (
            SELECT 1 FROM `tabPayment Transaction` pt
            WHERE pt.reference_name = ip.name
              AND pt.docstatus < 2
              AND pt.collected_by = %s
          )
        """
        args.append(collector)

    branch_clause, branch_params = _user_branch_clause("ip")
    args.extend(branch_params)

    # Aggregate by Installment Plan — one row per contract. Down payment (row 0,
    # installment_number=0) is split out so the UI shows "Первоначальный взнос" and
    # the regular installments separately.
    rows = frappe.db.sql(
        f"""
        SELECT
            ip.name AS installment_plan,
            ip.customer,
            {_collection_customer_select()},
            COALESCE(NULLIF(TRIM(ip.product_name), ''), '') AS product_name,
            MIN(isc.due_date) AS due_date,
            SUM(CASE WHEN COALESCE(isc.installment_number, 0) = 0
                     THEN (isc.amount - COALESCE(isc.paid_amount, 0))
                     ELSE 0 END) AS down_payment_due,
            SUM(CASE WHEN COALESCE(isc.installment_number, 0) > 0
                     THEN (isc.amount - COALESCE(isc.paid_amount, 0))
                     ELSE 0 END) AS amount_due,
            MAX(DATEDIFF(%s, isc.due_date)) AS days_overdue,
            SUM(CASE WHEN COALESCE(isc.installment_number, 0) > 0 THEN 1 ELSE 0 END)
                AS overdue_count,
            MAX((
                SELECT COALESCE(SUM(s.amount - COALESCE(s.paid_amount, 0)), 0)
                FROM `tabInstallment Schedule` s
                WHERE s.parent = ip.name
                  AND s.due_date <= %s
                  AND (s.amount - COALESCE(s.paid_amount, 0)) > 0.001
            )) AS total_debt_today,
            MAX(cl.outcome) AS last_call_outcome,
            MAX(cl.call_datetime) AS last_call_date,
            MAX(cl.promised_date) AS last_promised_date,
            MAX(cl.next_call_date) AS next_call_date,
            MAX(cl.notes) AS last_call_notes
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        LEFT JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        {_last_call_subquery()}
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          {collector_clause}
          {branch_clause}
        GROUP BY ip.name, ip.customer, cp.full_name, ip.product_name
        ORDER BY days_overdue DESC, amount_due DESC
        LIMIT {query_limit}
    """,
        tuple(args),
        as_dict=True,
    )

    for row in rows:
        row["amount_due"] = _to_float(row["amount_due"])
        row["down_payment_due"] = _to_float(row.get("down_payment_due"))
        row["total_debt_today"] = _to_float(row["total_debt_today"])
        row["days_overdue"] = cint(row["days_overdue"])
        row["overdue_count"] = cint(row["overdue_count"])
        row["phone"] = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": row["customer"], "is_primary": 1},
            "phone_number",
        )
    return rows


def _get_top_overdue_plans(limit=5, base_date=None):
    """Return top N overdue plans grouped at plan level (not per schedule row).

    Aggregates all overdue schedule rows per plan so a single plan with many
    overdue months doesn't consume multiple slots in 'Топ-5 просрочек'.
    """
    if base_date is None:
        base_date = getdate(today())
    n = _safe_limit(limit, default_value=5, max_value=20)
    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
    open_pred = _schedule_open_predicate(in_open)
    branch_clause, branch_params = _user_branch_clause("ip")

    rows = frappe.db.sql(
        f"""
        SELECT
            ip.name AS installment_plan,
            ip.customer,
            {_collection_customer_select()},
            COALESCE(NULLIF(TRIM(ip.product_name), ''), '') AS product_name,
            MAX(DATEDIFF(%s, isc.due_date)) AS days_overdue,
            SUM(isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due,
            cl.outcome AS last_call_outcome,
            cl.call_datetime AS last_call_date,
            cl.promised_date AS last_promised_date,
            cl.next_call_date AS next_call_date,
            cl.notes AS last_call_notes
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        LEFT JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        {_last_call_subquery()}
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          {branch_clause}
        GROUP BY ip.name, ip.customer, customer_name, ip.product_name,
                 cl.outcome, cl.call_datetime, cl.promised_date,
                 cl.next_call_date, cl.notes
        ORDER BY amount_due DESC, days_overdue DESC
        LIMIT {n}
        """,
        (base_date, base_date, *_OPEN_SCHEDULE_STATUSES, *branch_params),
        as_dict=True,
    )

    for row in rows:
        row["amount_due"] = _to_float(row["amount_due"])
        row["days_overdue"] = cint(row["days_overdue"])
        row["phone"] = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": row["customer"], "is_primary": 1},
            "phone_number",
        )
    return rows


@frappe.whitelist()
def get_due_today_list(limit=20, branch=None):
    query_limit = _safe_limit(limit, default_value=20, max_value=200)
    filters = ""
    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
    open_pred = _schedule_open_predicate(in_open)
    base_date = getdate(today())
    branch_clause, branch_params = _user_branch_clause("ip")
    # SQL placeholder order is: total_debt_today subquery date, then open_pred (3 statuses),
    # then isc.due_date in WHERE.
    args = [base_date, *_OPEN_SCHEDULE_STATUSES, base_date, *branch_params]

    # Aggregate by Installment Plan — one row per contract. Down payment (row 0,
    # installment_number=0) is split out from the regular installment sum.
    rows = frappe.db.sql(
        f"""
        SELECT
            ip.name AS installment_plan,
            ip.customer,
            {_collection_customer_select()},
            COALESCE(NULLIF(TRIM(ip.product_name), ''), '') AS product_name,
            MIN(isc.due_date) AS due_date,
            SUM(CASE WHEN COALESCE(isc.installment_number, 0) = 0
                     THEN (isc.amount - COALESCE(isc.paid_amount, 0))
                     ELSE 0 END) AS down_payment_due,
            SUM(CASE WHEN COALESCE(isc.installment_number, 0) > 0
                     THEN (isc.amount - COALESCE(isc.paid_amount, 0))
                     ELSE 0 END) AS amount_due,
            SUM(CASE WHEN COALESCE(isc.installment_number, 0) > 0 THEN 1 ELSE 0 END)
                AS due_count,
            MAX(CASE WHEN isc.status = 'Частично' THEN 1 ELSE 0 END) AS has_partial,
            MAX(cl.outcome) AS last_call_outcome,
            MAX(cl.call_datetime) AS last_call_date,
            MAX(cl.promised_date) AS last_promised_date,
            MAX(cl.next_call_date) AS next_call_date,
            MAX(cl.notes) AS last_call_notes,
            MAX((
                SELECT COALESCE(SUM(s.amount - COALESCE(s.paid_amount, 0)), 0)
                FROM `tabInstallment Schedule` s
                WHERE s.parent = ip.name
                  AND s.due_date <= %s
                  AND (s.amount - COALESCE(s.paid_amount, 0)) > 0.001
            )) AS total_debt_today
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        LEFT JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        {_last_call_subquery()}
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND {open_pred}
          AND isc.due_date = %s
          {filters}
          {branch_clause}
        GROUP BY ip.name, ip.customer, cp.full_name, ip.product_name
        ORDER BY has_partial DESC, amount_due DESC, down_payment_due DESC
        LIMIT {query_limit}
    """,
        tuple(args),
        as_dict=True,
    )
    for row in rows:
        row["amount_due"] = _to_float(row["amount_due"])
        row["down_payment_due"] = _to_float(row.get("down_payment_due"))
        row["total_debt_today"] = _to_float(row["total_debt_today"])
        row["due_count"] = cint(row["due_count"])
        row["urgency"] = "high" if cint(row.get("has_partial")) else "normal"
        row["phone"] = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": row["customer"], "is_primary": 1},
            "phone_number",
        )
    return rows


@frappe.whitelist()
def search_plans_by_imei(imei, limit=20):
    """Installment plans whose IMEI contains the given digits (partial match).

    Digits-only, minimum 3 digits, branch-scoped, read-only. Used by the BNPL
    panel's «Найти по IMEI» box.
    """
    term = _sanitize_imei_term(imei)
    if not term:
        return []

    branch_clause, branch_params = _user_branch_clause("ip")
    like = f"%{term}%"
    return frappe.db.sql(
        f"""
        SELECT ip.name, ip.customer, ip.customer_name, ip.status,
               ip.remaining_balance, ip.product_name, ip.imei
        FROM `tabInstallment Plan` ip
        WHERE ip.imei LIKE %s
          {branch_clause}
        ORDER BY ip.modified DESC
        LIMIT %s
        """,
        (like, *branch_params, _safe_limit(limit, 20, 50)),
        as_dict=True,
    )


_DEFAULT_PAYMENT_METHODS = [
    "Наличные USD", "Акксессуар касса", "Наличные UZS",
    "Карта", "Click", "Payme", "Перевод", "Терминал",
]


@frappe.whitelist()
def get_payment_methods():
    """Return the list of valid payment methods.

    Single source of truth for all JS dialogs.
    Priority: Merchant Settings.payment_methods → DocType meta → hardcoded fallback.
    Excludes 'Комбинированный' (computed, not selectable by the user).
    """
    _excluded = {"Комбинированный", "Наличные"}
    # 1. Merchant Settings (admin-editable, no migration needed)
    try:
        raw = frappe.db.get_single_value("Merchant Settings", "payment_methods") or ""
        methods = [m.strip() for m in raw.splitlines() if m.strip() and m.strip() not in _excluded]
        if methods:
            return methods
    except Exception:
        pass
    # 2. DocType meta fallback
    try:
        options = frappe.get_meta("Payment Transaction Line").get_field("payment_method").options or ""
        methods = [m.strip() for m in options.split("\n") if m.strip() and m.strip() not in _excluded]
        if methods:
            return methods
    except Exception:
        pass
    return _DEFAULT_PAYMENT_METHODS


@frappe.whitelist()
def get_recent_activity(limit=8):
    query_limit = _safe_limit(limit, default_value=8, max_value=50)

    recent_sales = frappe.get_all(
        "Sales Order",
        filters={"docstatus": 1},
        fields=["name", "customer", "total_amount", "creation"],
        order_by="creation desc",
        limit=query_limit,
    )
    recent_payments = frappe.get_all(
        "Payment Transaction",
        filters={"status": "Завершен"},
        fields=["name", "customer", "amount", "payment_date", "creation"],
        order_by="creation desc",
        limit=query_limit,
    )
    new_clients = frappe.get_all(
        "Customer Profile",
        fields=["name", "full_name", "creation", "status", "total_debt"],
        order_by="creation desc",
        limit=query_limit,
    )
    return {
        "recent_sales": recent_sales,
        "recent_payments": recent_payments,
        "new_clients": new_clients,
    }


@frappe.whitelist()
def get_client_risk_snapshot(customer):
    _require_doc_permission("Customer Profile", customer, "read")
    doc = frappe.get_doc("Customer Profile", customer)
    active_plans = frappe.db.count(
        "Installment Plan",
        {"customer": customer, "docstatus": 1, "status": ["in", ["Активный", "Просрочен"]]},
    )
    overdue_count = frappe.db.count(
        "Installment Schedule",
        {"parenttype": "Installment Plan", "status": "Просрочен", "parent": ["in", frappe.get_all("Installment Plan", filters={"customer": customer}, pluck="name") or [""]]},
    )
    risk_score = cint(doc.get("risk_score") or 0)
    risk_level = doc.get("risk_level") or _risk_level_from_score(risk_score)
    primary_phone = ""
    phones = frappe.get_all(
        "Customer Phone Number",
        filters={"parent": customer},
        fields=["phone_number"],
        order_by="idx asc",
        limit=1,
    )
    if phones:
        primary_phone = (phones[0].phone_number or "").strip()
    credit_limit = _to_float(doc.credit_limit)
    unlimited = credit_limit <= 0
    return {
        "customer": doc.name,
        "full_name": doc.full_name,
        "primary_phone": primary_phone,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "active_loans": active_plans,
        "overdue_loans": overdue_count,
        "total_debt": _to_float(doc.total_debt),
        "available_limit": None if unlimited else _to_float(doc.available_limit),
        "unlimited_credit": unlimited,
    }


@frappe.whitelist()
def get_installment_suggestions(customer=None, product=None, amount=None):
    # Branch-scoped read on the customer if provided, so users can't enumerate
    # other branches' customer income data via this endpoint.
    if customer:
        _require_doc_permission("Customer Profile", customer, "read")
    if amount is None and product:
        amount = frappe.db.get_value("Product", product, "selling_price")
    amount = _to_float(amount)
    if amount <= 0:
        return {"recommended_months": 6, "options": [3, 6, 12]}

    monthly_income = 0
    if customer:
        monthly_income = _to_float(frappe.db.get_value("Customer Profile", customer, "monthly_income"))

    options = [3, 6, 12]
    monthly_buckets = []
    for months in options:
        monthly = amount / months
        monthly_buckets.append({"months": months, "monthly_payment": monthly})

    recommended = 6
    if monthly_income:
        affordable = [o for o in monthly_buckets if o["monthly_payment"] <= (monthly_income * 0.4)]
        if affordable:
            recommended = affordable[0]["months"]
        else:
            recommended = 12

    return {"recommended_months": recommended, "options": options, "buckets": monthly_buckets}


def _merge_timeline_events(limit=24):
    """Single chronological feed for the control center (payments, sales, clients, missed)."""
    ql = _safe_limit(limit, default_value=24, max_value=80)
    per = max(ql // 4, 6)

    payments = frappe.get_all(
        "Payment Transaction",
        filters={"status": "Завершен"},
        fields=["name", "customer", "amount", "payment_date", "creation"],
        order_by="creation desc",
        limit=per,
    )
    sales = frappe.get_all(
        "Sales Order",
        filters={"docstatus": 1},
        fields=["name", "customer", "total_amount", "creation"],
        order_by="creation desc",
        limit=per,
    )
    clients = frappe.get_all(
        "Customer Profile",
        fields=["name", "full_name", "creation"],
        order_by="creation desc",
        limit=per,
    )
    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
    open_pred = _schedule_open_predicate(in_open)
    base_td = getdate(today())
    branch_clause, branch_params = _user_branch_clause("ip")
    missed = frappe.db.sql(
        f"""
        SELECT
            isc.name AS schedule_ref,
            isc.modified,
            isc.due_date,
            ip.name AS installment_plan,
            ip.customer,
            {_collection_customer_select()},
            (isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due,
            DATEDIFF(%s, isc.due_date) AS days_overdue
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        LEFT JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          {branch_clause}
        ORDER BY isc.modified DESC
        LIMIT {per}
        """,
        (base_td, base_td, *_OPEN_SCHEDULE_STATUSES, *branch_params),
        as_dict=True,
    )

    events = []
    for row in payments:
        ts = row.get("creation") or row.get("payment_date")
        events.append(
            {
                "kind": "payment_received",
                "tone": "success",
                "timestamp": str(ts),
                "title": _("Платеж получен"),
                "detail": f"{row.name} · {_to_float(row.amount):,.0f}",
                "reference": row.name,
            }
        )
    for row in sales:
        events.append(
            {
                "kind": "new_sale",
                "tone": "neutral",
                "timestamp": str(row.creation),
                "title": _("Новая продажа"),
                "detail": f"{row.name} · {_to_float(row.total_amount):,.0f}",
                "reference": row.name,
            }
        )
    for row in clients:
        events.append(
            {
                "kind": "new_client",
                "tone": "info",
                "timestamp": str(row.creation),
                "title": _("Новый клиент"),
                "detail": row.get("full_name") or row.name,
                "reference": row.name,
            }
        )
    for row in missed:
        row["amount_due"] = _to_float(row.get("amount_due"))
        events.append(
            {
                "kind": "missed_payment",
                "tone": "risk",
                "timestamp": str(row.get("modified")),
                "title": _("Просроченный платеж"),
                "detail": f"{row.get('customer_name') or row.get('customer')} · {row['amount_due']:,.0f} · {cint(row.get('days_overdue') or 0)} {_('дн.')}",
                "reference": row.get("installment_plan"),
            }
        )

    events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return events[:ql]


@frappe.whitelist()
def get_activity_timeline(limit=24):
    return {"events": _merge_timeline_events(limit=limit)}


@frappe.whitelist()
def send_due_payment_reminder(installment_plan, schedule_name=None, amount=None):
    """Send a one-off SMS reminder for a due installment (uses SMS Gateway Settings)."""
    _require_doc_permission("Installment Plan", installment_plan, "write")
    customer = frappe.db.get_value("Installment Plan", installment_plan, "customer")
    if not customer:
        frappe.throw(_("Клиент не найден"))

    phone = frappe.db.get_value(
        "Customer Phone Number",
        {"parent": customer, "is_primary": 1},
        "phone_number",
    )
    if not phone:
        frappe.throw(_("У клиента не указан основной номер"))

    amt = _to_float(amount)
    if amt <= 0 and schedule_name:
        amt = _to_float(frappe.db.get_value("Installment Schedule", schedule_name, "amount")) - _to_float(
            frappe.db.get_value("Installment Schedule", schedule_name, "paid_amount")
        )
    if amt <= 0:
        in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
        open_pred = _schedule_open_predicate(in_open)
        amt = _to_float(
            frappe.db.sql(
                f"""
                SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
                FROM `tabInstallment Schedule` isc
                WHERE isc.parent = %s
                  AND {open_pred}
                  AND isc.due_date = %s
                """,
                (installment_plan, *_OPEN_SCHEDULE_STATUSES, getdate(today())),
            )[0][0]
        )

    from nasiya365.utils.sms_manager import SMSManager

    message = _("Напоминание Nasiya365: сегодня ожидается платеж {0:,.0f} сум.").format(amt)
    sms = SMSManager()
    ok = sms.send_sms(phone, message)
    if not ok:
        frappe.msgprint(_("SMS не отправлено: проверьте настройки шлюза"), indicator="orange")
    return {"sent": bool(ok)}


@frappe.whitelist()
def get_control_center_snapshot(date=None):
    """
    Aggregated payload for the BNPL financial control center (single round-trip).
    """
    base_date = getdate(date) if date else getdate(today())
    yesterday = add_days(base_date, -1)

    cur = _kpi_metrics(base_date)
    yday = _kpi_metrics(yesterday)

    outstanding = cur["outstanding_amount"]
    overdue_amt = cur["overdue_amount"]
    overdue_pct_portfolio = round((overdue_amt / outstanding) * 100, 2) if outstanding else 0.0

    prev_start, prev_end = _revenue_mtd_prev_month_window(base_date)
    revenue_prev_mtd = _sum_payments_between(prev_start, prev_end)

    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
    open_pred = _schedule_open_predicate(in_open)
    branch_clause, branch_params = _user_branch_clause("ip")
    overdue_lines = frappe.db.sql(
        f"""
        SELECT COUNT(*)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          {branch_clause}
    """,
        (base_date, *_OPEN_SCHEDULE_STATUSES, *branch_params),
    )[0][0]

    high_risk_clients = frappe.db.sql(
        f"""
        SELECT COUNT(DISTINCT ip.customer)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0.001
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          AND DATEDIFF(%s, isc.due_date) > 7
          {branch_clause}
    """,
        (base_date, *_OPEN_SCHEDULE_STATUSES, base_date, *branch_params),
    )[0][0]

    due_today = cur["due_today_amount"]
    collected = cur["cash_collected_today"]
    if due_today > 0:
        collection_progress_pct = min(100.0, round((collected / due_today) * 100, 1))
    else:
        collection_progress_pct = 100.0 if collected > 0 else 0.0

    top_overdue = _get_top_overdue_plans(limit=5, base_date=base_date)

    kpi_cards = [
        {
            "id": "outstanding",
            "label": _("Всего к получению"),
            "value": outstanding,
            "subtext": _("Активные рассрочки"),
            "tone": "neutral",
            "trend": None,
            "route": ["List", "Installment Plan", {"status": ["in", ["Активный", "Просрочен"]]}],
        },
        {
            "id": "overdue",
            "label": _("Просрочено"),
            "value": overdue_amt,
            "subtext": _("{0}% от портфеля").format(overdue_pct_portfolio),
            "tone": "risk",
            "trend": None,
            "route": ["app", "overdue-collector"],
        },
        {
            "id": "due_today",
            "label": _("Ожидается сегодня"),
            "value": due_today,
            "subtext": _("Плановые платежи"),
            "tone": "pending",
            "trend": None,
            "route": ["List", "Installment Plan"],
        },
        {
            "id": "collected_today",
            "label": _("Собрано сегодня"),
            "value": collected,
            "subtext": _("Фактически получено"),
            "tone": "success",
            "trend": _trend(collected, yday["cash_collected_today"]),
            "route": ["List", "Payment Transaction", {"payment_date": str(base_date)}],
        },
        {
            "id": "active_contracts",
            "label": _("Активные договоры"),
            "value": cur["active_contracts"],
            "subtext": _("Количество планов"),
            "tone": "neutral",
            "trend": None,
            "route": ["List", "Installment Plan", {"status": ["in", ["Активный", "Просрочен"]]}],
        },
        {
            "id": "revenue_mtd",
            "label": _("Выручка (МТД)"),
            "value": cur["revenue_mtd"],
            "subtext": _("Завершенные платежи"),
            "tone": "success",
            "trend": _trend(cur["revenue_mtd"], revenue_prev_mtd),
            "route": ["List", "Payment Transaction"],
        },
    ]

    needs_attention = {
        "overdue_payments_count": cint(overdue_lines or 0),
        "high_risk_clients": cint(high_risk_clients or 0),
        "amount_at_risk": overdue_amt,
        "top_overdue": top_overdue,
    }

    cashflow = {
        "expected_today": due_today,
        "collected_today": collected,
        "progress_pct": collection_progress_pct,
    }

    due_actions = get_due_today_list(limit=15)

    return {
        "as_of": str(base_date),
        "kpi_cards": kpi_cards,
        "needs_attention": needs_attention,
        "cashflow": cashflow,
        "due_today_actions": due_actions,
        "activity": {"events": _merge_timeline_events(limit=24)},
        "future_modules": {
            "risk_scoring": {"status": "planned", "hint": _("Расширение профиля риска клиента")},
            "fraud_signals": {"status": "planned", "hint": _("Сигналы мошенничества и аномалий")},
            "forecasting": {"status": "planned", "hint": _("Прогноз поступлений и дефолта")},
            "collection_kpis": {"status": "planned", "hint": _("Эффективность коллекшн-команды")},
        },
    }


@frappe.whitelist()
def accept_overdue_payment(customer_or_plan=None, amount=None, mode="Наличные USD", payment_date=None):
    if not customer_or_plan:
        frappe.throw(_("Укажите клиента или план"))
    amount = _to_float(amount)
    if amount <= 0:
        frappe.throw(_("Сумма платежа должна быть больше нуля"))

    plan_name = customer_or_plan
    if not frappe.db.exists("Installment Plan", plan_name):
        plan_name = frappe.db.get_value(
            "Installment Plan",
            {"customer": customer_or_plan, "docstatus": 1, "status": ["in", ["Активный", "Просрочен"]]},
            "name",
        )
    if not plan_name:
        frappe.throw(_("Не найден активный план рассрочки"))
    _require_doc_permission("Installment Plan", plan_name, "write")

    from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
        _normalize_payment_line_method,
    )

    customer = frappe.db.get_value("Installment Plan", plan_name, "customer")
    payment = frappe.new_doc("Payment Transaction")
    payment.customer = customer
    payment.payment_method = _normalize_payment_line_method(mode)
    payment.payment_date = payment_date or nowdate()
    payment.reference_doctype = "Installment Plan"
    payment.reference_name = plan_name
    payment.received_by = frappe.session.user
    payment.append(
        "payment_lines",
        {
            "payment_method": _normalize_payment_line_method(mode),
            "currency": "USD",
            "amount": amount,
        },
    )
    payment.insert(ignore_permissions=True)
    # Submit so on_submit fires (sets status=Завершен, allocates to plan, syncs cashbox).
    payment.submit()
    frappe.db.commit()
    return {"name": payment.name, "plan": plan_name}


def _norm_imei_sql(col):
    """Normalized IMEI SQL expression (trim, upper, strip spaces) for exact matching."""
    return f"REPLACE(UPPER(TRIM({col})), ' ', '')"


def _serial_reserved_sql(imei_expr, so_excl_sql="''", plan_excl_sql="''"):
    """
    SQL boolean that is TRUE when the serial `imei_expr` is currently RESERVED — i.e.
    sold / issued and NOT bought back.

    Rule: the serial is reserved iff there is a CONSUMPTION (submitted-or-draft Sales
    Order, open Installment Plan, or warehouse issue «Отпуск») that has NO later
    ACQUISITION («Поступление»/«Корректировка», docstatus=1) of the same serial. A
    buy-back (acquisition dated after the sale) therefore frees the serial again, while
    a mere duplicate receipt (dated before the sale) does not — so a phone can never be
    sold twice without a real re-acquisition in between.

    Fail-safe on dirty data: a consumption with a missing date is treated as the most
    recent event (stays reserved); an acquisition with a missing date never frees it.
    Matching is exact on the normalized IMEI (no last-6 fuzz), so a different phone that
    happens to share the last six digits never interferes.

      imei_expr     : SQL for the target normalized IMEI ('%(imei)s' or NORM(col)).
      so_excl_sql   : SQL for the Sales Order name to exclude ('%(so)s' or "''").
      plan_excl_sql : SQL for the Installment Plan name to exclude.
    """
    ni = imei_expr

    def _no_later_acquisition(cons_date_sql):
        # No «Поступление»/«Корректировка» of this serial dated after the consumption.
        # NULL acquisition date never frees; NULL consumption date stays reserved.
        return f"""NOT EXISTS (
            SELECT 1 FROM `tabStock Entry Item` a_i
            JOIN `tabStock Entry` a_e ON a_e.name = a_i.parent
            WHERE a_e.docstatus = 1
              AND a_e.entry_type IN ('Поступление', 'Корректировка')
              AND NULLIF(TRIM(a_i.imei), '') IS NOT NULL
              AND {_norm_imei_sql('a_i.imei')} = {ni}
              AND COALESCE(a_e.posting_date, '0001-01-01') > COALESCE({cons_date_sql}, '9999-12-31')
        )"""

    # Correlated EXISTS (no derived table -> works without LATERAL). Reserved iff some
    # consumption of the serial has no later acquisition.
    return f"""
    (
        EXISTS (
            SELECT 1 FROM `tabSales Order Item` soi
            JOIN `tabSales Order` so ON so.name = soi.parent
            WHERE IFNULL(so.docstatus, 0) < 2
              AND ({so_excl_sql} = '' OR so.name != {so_excl_sql})
              AND NULLIF(TRIM(soi.imei), '') IS NOT NULL
              AND {_norm_imei_sql('soi.imei')} = {ni}
              AND {_no_later_acquisition('so.order_date')}
        )
        OR EXISTS (
            SELECT 1 FROM `tabInstallment Plan` ip
            WHERE IFNULL(ip.docstatus, 0) < 2
              AND IFNULL(ip.contract_status, '') != 'Отменен'
              AND ({plan_excl_sql} = '' OR ip.name != {plan_excl_sql})
              AND NULLIF(TRIM(ip.imei), '') IS NOT NULL
              AND {_norm_imei_sql('ip.imei')} = {ni}
              AND {_no_later_acquisition('ip.start_date')}
        )
        OR EXISTS (
            SELECT 1 FROM `tabStock Entry Item` oxi
            JOIN `tabStock Entry` ox ON ox.name = oxi.parent
            WHERE ox.docstatus = 1 AND ox.entry_type = 'Отпуск'
              AND NULLIF(TRIM(oxi.imei), '') IS NOT NULL
              AND {_norm_imei_sql('oxi.imei')} = {ni}
              AND {_no_later_acquisition('ox.posting_date')}
        )
    )
    """


def _purchase_serial_tracks_consumed(serial_raw, exclude_installment_plan_name="", exclude_sales_order_name=""):
    """
    TRUE when the serial is currently RESERVED (sold / issued and not bought back), so a
    new sale of it must be blocked. Kept name + signature so all callers stay unchanged.
    See _serial_reserved_sql for the exact rule (buy-back-aware, fail-safe, exact IMEI).
    """
    s = (serial_raw or "").strip().replace(" ", "").upper()
    if not s:
        return False
    sql = "SELECT " + _serial_reserved_sql("%(imei)s", "%(so)s", "%(plan)s")
    res = frappe.db.sql(
        sql,
        {
            "imei": s,
            "so": exclude_sales_order_name or "",
            "plan": exclude_installment_plan_name or "",
        },
    )
    return bool(res and res[0][0])


def installment_plan_stock_ref_to_parent(ref: str | None) -> str | None:
    """Resolve Installment Plan `stock_entry` to parent Stock Entry name (legacy or Stock Entry Item row)."""
    if not ref:
        return None
    if frappe.db.exists("Stock Entry", ref):
        return ref
    if frappe.db.exists("Stock Entry Item", ref):
        return frappe.db.get_value("Stock Entry Item", ref, "parent")
    return None


def installment_plan_stock_ref_is_sei_row(ref: str | None) -> bool:
    return bool(ref and frappe.db.exists("Stock Entry Item", ref))


def installment_plan_taken_imei_rows_for_receipt(stock_ref, current_plan_name=None):
    """IMEIs already tied to other open plans on the same receipt (legacy parent or SEI link)."""
    parent = installment_plan_stock_ref_to_parent(stock_ref) or stock_ref
    if not parent:
        return []
    cp = current_plan_name or ""
    return frappe.db.sql(
        """SELECT ip.imei FROM `tabInstallment Plan` ip
           WHERE IFNULL(ip.docstatus, 0) < 2
             AND (%s = '' OR ip.name != %s)
             AND NULLIF(TRIM(ip.imei), '') IS NOT NULL
             AND (
               ip.stock_entry = %s
               OR EXISTS (
                 SELECT 1 FROM `tabStock Entry Item` sei0
                 WHERE sei0.parent = %s AND sei0.name = ip.stock_entry
               )
             )""",
        (cp, cp, parent, parent),
        as_dict=True,
    )


def assert_stock_entry_available_for_installment_plan(stock_entry, current_plan_name=None, current_sales_order_name=None):
    """
    Submitted Поступление only; positive stock in ledger; not marked sold/return;
    serial not already sold (SO, including drafts / Отпуск / other plan IMEI); not linked to another open plan.

    `stock_entry` may be a Stock Entry name (legacy) or a Stock Entry Item row name (one line).
    """
    if not stock_entry:
        return
    parent = installment_plan_stock_ref_to_parent(stock_entry)
    if not parent:
        frappe.throw(_("Документ поступления не найден"))
    row = frappe.db.get_value(
        "Stock Entry",
        parent,
        ["docstatus", "entry_type", "business_status", "warehouse"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("Документ поступления не найден"))
    if row.docstatus != 1:
        frappe.throw(_("Выберите проведённое поступление на склад"))
    if row.entry_type != "Поступление":
        frappe.throw(_("Для рассрочки доступны только операции типа «Поступление»"))
    bs = (row.business_status or "").strip()
    # "Рассрочка" is set by this plan itself after submit — allow when updating an existing plan.
    # "Наличные" / "Возврат" are always final and must be blocked.
    if bs in _STOCK_ENTRY_UNAVAILABLE_BUSINESS:
        if bs != "Рассрочка" or not current_plan_name:
            frappe.throw(
                _("Это поступление уже отражено как продажа или возврат. Выберите другой документ.")
            )
    if installment_plan_stock_ref_is_sei_row(stock_entry):
        sei = frappe.db.get_value(
            "Stock Entry Item",
            stock_entry,
            ["product", "imei", "parent"],
            as_dict=True,
        )
        if not sei or sei.parent != parent:
            frappe.throw(_("Строка поступления не найдена"))
    else:
        sei = frappe.db.get_value(
            "Stock Entry Item",
            {"parent": parent, "idx": 1},
            ["product", "imei"],
            as_dict=True,
        )
    if sei and sei.product and row.warehouse:
        bal = frappe.db.sql(
            """
            SELECT COALESCE(SUM(quantity_change), 0) FROM `tabStock Ledger`
            WHERE product = %s AND warehouse = %s
            """,
            (sei.product, row.warehouse),
        )[0][0]
        if flt(bal) <= 0:
            frappe.throw(
                _("По этому складу для товара нет остатка — единица уже списана (продажа или отпуск).")
            )

    if sei and sei.imei and str(sei.imei).strip():
        if _purchase_serial_tracks_consumed(sei.imei, current_plan_name or "", current_sales_order_name or ""):
            frappe.throw(
                _(
                    "Этот серийный номер уже в продаже (заказ), отпуске или другом плане рассрочки. Выберите другое поступление."
                )
            )


def _stock_entry_item_row_free_sql():
    """
    SQL fragment: one `sei` row is still available for a BNPL link.

    Available = product has positive stock in the ledger, the serial is not currently
    reserved (sold/issued and not bought back — see _serial_reserved_sql, buy-back aware),
    and this exact row is not already linked to another open plan. Kept consistent with
    the Python `_purchase_serial_tracks_consumed` so the picker and save-validation agree.
    The two `%s` from the reserved fragment and the two from the per-row check are all the
    same plan name, so their order in the args tuple does not matter.
    """
    reserved = _serial_reserved_sql(_norm_imei_sql("sei.imei"), so_excl_sql="''", plan_excl_sql="%s")
    return f"""
      IFNULL((
          SELECT SUM(sl.quantity_change) FROM `tabStock Ledger` sl
          WHERE sl.product = sei.product AND sl.warehouse = se.warehouse
      ), 0) > 0
      AND (
          NULLIF(TRIM(sei.imei), '') IS NULL
          OR NOT ({reserved})
      )
      AND NOT EXISTS (
          SELECT 1 FROM `tabInstallment Plan` ipx
          WHERE IFNULL(ipx.docstatus, 0) < 2
            AND (%s = '' OR ipx.name != %s)
            AND NULLIF(TRIM(ipx.imei), '') IS NOT NULL
            AND NULLIF(TRIM(sei.imei), '') IS NOT NULL
            AND (
              ipx.stock_entry = sei.name
              OR (
                ipx.stock_entry = se.name
                AND {_norm_imei_sql('ipx.imei')} = {_norm_imei_sql('sei.imei')}
              )
            )
      )
    """


@frappe.whitelist()
def installment_plan_stock_entry_query(
    doctype,
    txt,
    searchfield,
    start,
    page_length,
    filters,
    as_dict=False,
    reference_doctype=None,
    ignore_user_permissions=False,
    link_fieldname=None,
):
    """Link search: one row per Stock Entry that has at least one free item."""
    if isinstance(filters, str):
        filters = json.loads(filters) if filters else {}
    plan_name = (filters or {}).get("installment_plan") or ""

    start, page_length = cint(start), cint(page_length)

    row_free = _stock_entry_item_row_free_sql()
    args = [
        "Поступление",
        plan_name,
        plan_name,
        plan_name,
        plan_name,
    ]

    txt_filter = ""
    if txt:
        txt_filter = "AND (se.name LIKE %s OR se.items_summary LIKE %s)"
        t = f"%{txt}%"
        args.extend([t, t])

    args.extend([page_length, start])

    sql = f"""
        SELECT
            se.name,
            CONCAT(
                IFNULL(NULLIF(TRIM(se.items_summary), ''), se.name),
                ' · ',
                IFNULL(se.warehouse, ''),
                ' (',
                se.posting_date,
                ')'
            )
        FROM `tabStock Entry` se
        WHERE se.docstatus = 1
          AND se.entry_type = %s
          AND IFNULL(NULLIF(TRIM(se.business_status), ''), 'В наличии') NOT IN (
              'Наличные', 'Рассрочка', 'Возврат'
          )
          AND EXISTS (
              SELECT 1 FROM `tabStock Entry Item` sei
              WHERE sei.parent = se.name
                AND ({row_free})
          )
          {txt_filter}
        ORDER BY se.modified DESC
        LIMIT %s OFFSET %s
    """

    return frappe.db.sql(sql, tuple(args), as_list=1)


def _imei_matches_preference(serial: str, preferred: str) -> bool:
    if not preferred or not serial:
        return False
    a = (serial or "").strip().upper().replace(" ", "")
    b = (preferred or "").strip().upper().replace(" ", "")
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 6 and len(b) >= 6 and a[-6:] == b[-6:]:
        return True
    return False


@frappe.whitelist()
def get_stock_entry_details_for_installment_plan(
    stock_entry, installment_plan=None, preferred_imei=None, sales_order=None
):
    """
    Installment Plan form: auto-fill principal / product / serial from a submitted receipt.
    Implemented under api.* (not doctype controller) so desk RPC always resolves the method.
    If preferred_imei is set (e.g. user picked a specific line in the link search), narrow to that row.
    `sales_order` (when called from the Sales Order form) excludes that draft's own picked
    items from the "already taken" check, so re-selecting the same receipt doesn't self-block.
    """
    if not stock_entry:
        return {}
    # Caller passes either a Stock Entry name (legacy) or a Stock Entry Item row name.
    # Resolve to the parent Stock Entry FIRST so the read permission check uses the
    # actual doctype that branch filtering applies to.
    parent = installment_plan_stock_ref_to_parent(stock_entry)
    if not parent:
        return {}
    _require_doc_permission("Stock Entry", parent, "read")
    current_plan = installment_plan or ""
    current_so = sales_order or ""
    assert_stock_entry_available_for_installment_plan(stock_entry, current_plan, current_so)
    ste = frappe.get_doc("Stock Entry", parent)
    is_line = installment_plan_stock_ref_is_sei_row(stock_entry)

    taken_rows = installment_plan_taken_imei_rows_for_receipt(stock_entry, current_plan)
    taken = {(r.imei or "").strip().upper().replace(" ", "")[-6:] for r in taken_rows if r.imei}

    # Collect all free items (not yet taken by another plan, draft Sales Order, or sale)
    free_items = []
    for item in ste.items:
        if is_line and item.name != stock_entry:
            continue
        serial = (item.imei or "").strip()
        if serial:
            if serial.upper().replace(" ", "")[-6:] in taken:
                continue  # already linked to another plan
            if _purchase_serial_tracks_consumed(serial, current_plan, current_so):
                continue  # already sold, issued, or reserved by another order/plan
        product_name = (
            frappe.db.get_value("Product", item.product, "product_name") if item.product else None
        )
        free_items.append({
            "product": item.product,
            "product_name": product_name,
            "imei": serial,
            "amount": flt(item.amount),
            "color": (item.color or "").strip(),
            "storage": (item.storage or "").strip(),
            "condition": (item.condition or "").strip(),
        })

    pref = (preferred_imei or "").strip()
    if pref and len(free_items) > 1:
        matched = [it for it in free_items if _imei_matches_preference(it.get("imei") or "", pref)]
        if len(matched) >= 1:
            free_items = matched[:1]

    first = free_items[0] if free_items else None
    total = flt(ste.total_value) or sum(flt(i.amount) for i in ste.items)
    result = {
        "total_amount": first["amount"] if first else total,
        "product_name": first["product_name"] if first else None,
        "imei": first["imei"] if first else None,
        "customer": None,
        "free_items": free_items,
    }
    return result


@frappe.whitelist()
def get_cashbox_reconciliation(cashbox, date=None):
    """
    End-of-day reconciliation: compare cashbox transaction rows (ledger)
    against Payment Transactions posted for the same cashbox on the given date.

    Returns:
      - ledger_totals: per payment_method totals from Cashbox Transactions
      - payment_totals: per payment_method totals from Payment Transaction Lines
      - discrepancies: methods where the two differ by > 0.01
    """
    report_date = getdate(date) if date else getdate(today())

    # --- Ledger side: cashbox transaction rows for this date ---
    ledger_rows = frappe.db.sql(
        """
        SELECT
            ct.payment_method,
            ct.currency,
            SUM(CASE WHEN ct.transaction_type = 'Приход' THEN ct.amount ELSE -ct.amount END) AS net_amount,
            COALESCE(MAX(ct.exchange_rate), 0) AS exchange_rate
        FROM `tabCashbox Transaction` ct
        WHERE ct.parent = %s
          AND DATE(ct.creation) = %s
        GROUP BY ct.payment_method, ct.currency
        """,
        (cashbox, report_date),
        as_dict=True,
    )

    # --- Payment side: payment transaction lines that reference this cashbox ---
    payment_rows = frappe.db.sql(
        """
        SELECT
            ptl.payment_method,
            COALESCE(ptl.currency, 'USD') AS currency,
            SUM(ptl.amount) AS net_amount,
            COALESCE(MAX(ptl.exchange_rate), 0) AS exchange_rate
        FROM `tabPayment Transaction Line` ptl
        INNER JOIN `tabPayment Transaction` pt ON pt.name = ptl.parent
        WHERE pt.cashbox = %s
          AND DATE(pt.creation) = %s
          AND (pt.status IS NULL OR pt.status != 'Отменен')
        GROUP BY ptl.payment_method, COALESCE(ptl.currency, 'USD')
        """,
        (cashbox, report_date),
        as_dict=True,
    )

    def _to_usd(rows):
        """Collapse to a {method: usd_amount} dict."""
        result = {}
        for r in rows:
            method = r.payment_method or "Другое"
            currency = (r.currency or "USD").upper()
            amount = flt(r.net_amount)
            if currency == "UZS":
                rate = flt(r.exchange_rate) or 12200
                amount = amount / rate
            result[method] = round(result.get(method, 0.0) + amount, 2)
        return result

    ledger_by_method = _to_usd(ledger_rows)
    payment_by_method = _to_usd(payment_rows)

    all_methods = set(ledger_by_method) | set(payment_by_method)
    discrepancies = []
    for method in sorted(all_methods):
        ledger_amt = ledger_by_method.get(method, 0.0)
        payment_amt = payment_by_method.get(method, 0.0)
        diff = round(ledger_amt - payment_amt, 2)
        if abs(diff) > 0.01:
            discrepancies.append({
                "method": method,
                "ledger": ledger_amt,
                "payments": payment_amt,
                "diff": diff,
            })

    return {
        "date": str(report_date),
        "cashbox": cashbox,
        "ledger_totals": ledger_by_method,
        "payment_totals": payment_by_method,
        "discrepancies": discrepancies,
        "is_balanced": len(discrepancies) == 0,
    }
