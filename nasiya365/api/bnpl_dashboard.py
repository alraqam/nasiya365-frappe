import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, get_last_day, getdate, nowdate, today

# Поступление already consumed (cash/BNPL issue) or returned — hide from new installment plans.
_STOCK_ENTRY_UNAVAILABLE_BUSINESS = ("Naqd savdo", "Rassrochka savdo", "Возврат")

# Installment Schedule child default was "Pending" while valid options are Russian — include both in SQL.
_OPEN_SCHEDULE_STATUSES = ("Ожидает", "Частично", "Pending")

# Collector lists: not only submitted (1); drafts often stay docstatus 0 until first payment flow.
_COLLECTION_PLAN_WHERE = (
    "ip.docstatus < 2 AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан')"
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
    return _to_float(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(pt.amount), 0)
            FROM `tabPayment Transaction` pt
            WHERE pt.docstatus < 2
              AND pt.status = 'Завершен'
              AND pt.payment_date BETWEEN %s AND %s
            """,
            (start, end),
        )[0][0]
    )


def _kpi_metrics(base_date, branch=None):
    outstanding = frappe.db.sql(
        """
        SELECT COALESCE(SUM(ip.remaining_balance), 0)
        FROM `tabInstallment Plan` ip
        WHERE ip.docstatus = 1
          AND ip.status IN ('Активный', 'Просрочен')
    """,
        (),
    )[0][0]

    in_open = ",".join(["%s"] * len(_OPEN_SCHEDULE_STATUSES))
    open_pred = _schedule_open_predicate(in_open)
    due_today = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND {open_pred}
          AND isc.due_date = %s
    """,
        (*_OPEN_SCHEDULE_STATUSES, base_date),
    )[0][0]

    overdue = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
    """,
        (base_date, *_OPEN_SCHEDULE_STATUSES),
    )[0][0]

    collected_today = frappe.db.sql(
        """
        SELECT COALESCE(SUM(pt.amount), 0)
        FROM `tabPayment Transaction` pt
        WHERE pt.docstatus < 2
          AND pt.status = 'Завершен'
          AND pt.payment_date = %s
    """,
        (base_date,),
    )[0][0]

    active_contracts = frappe.db.count(
        "Installment Plan",
        {"docstatus": 1, "status": ["in", ["Активный", "Просрочен"]]},
    )

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
    if not installment_plan:
        frappe.throw("installment_plan is required")
    customer = frappe.db.get_value("Installment Plan", installment_plan, "customer")
    if not customer:
        frappe.throw("Installment Plan not found")

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

    rows = frappe.db.sql(
        f"""
        SELECT
            ip.name AS installment_plan,
            ip.customer,
            {_collection_customer_select()},
            COALESCE(NULLIF(TRIM(ip.product_name), ''), '') AS product_name,
            isc.name AS schedule_name,
            isc.due_date,
            (isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due,
            DATEDIFF(%s, isc.due_date) AS days_overdue,
            (
                SELECT COALESCE(SUM(s.amount - COALESCE(s.paid_amount, 0)), 0)
                FROM `tabInstallment Schedule` s
                WHERE s.parent = ip.name
                  AND s.due_date <= %s
                  AND (s.amount - COALESCE(s.paid_amount, 0)) > 0
            ) AS total_debt_today,
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
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          {collector_clause}
        ORDER BY days_overdue DESC, amount_due DESC
        LIMIT {query_limit}
    """,
        tuple(args),
        as_dict=True,
    )

    for row in rows:
        row["amount_due"] = _to_float(row["amount_due"])
        row["total_debt_today"] = _to_float(row["total_debt_today"])
        row["days_overdue"] = cint(row["days_overdue"])
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
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
        GROUP BY ip.name, ip.customer, customer_name, ip.product_name,
                 cl.outcome, cl.call_datetime, cl.promised_date,
                 cl.next_call_date, cl.notes
        ORDER BY amount_due DESC, days_overdue DESC
        LIMIT {n}
        """,
        (base_date, base_date, *_OPEN_SCHEDULE_STATUSES),
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
    args = [*_OPEN_SCHEDULE_STATUSES, base_date, base_date]

    rows = frappe.db.sql(
        f"""
        SELECT
            ip.name AS installment_plan,
            ip.customer,
            {_collection_customer_select()},
            COALESCE(NULLIF(TRIM(ip.product_name), ''), '') AS product_name,
            isc.name AS schedule_name,
            isc.due_date,
            isc.status AS schedule_status,
            (isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due,
            cl.outcome AS last_call_outcome,
            cl.call_datetime AS last_call_date,
            cl.promised_date AS last_promised_date,
            cl.next_call_date AS next_call_date,
            cl.notes AS last_call_notes,
            (
                SELECT COALESCE(SUM(s.amount - COALESCE(s.paid_amount, 0)), 0)
                FROM `tabInstallment Schedule` s
                WHERE s.parent = ip.name
                  AND s.due_date <= %s
                  AND (s.amount - COALESCE(s.paid_amount, 0)) > 0
            ) AS total_debt_today
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        LEFT JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        {_last_call_subquery()}
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND {open_pred}
          AND isc.due_date = %s
          {filters}
        ORDER BY
            CASE isc.status WHEN 'Частично' THEN 0 ELSE 1 END,
            amount_due DESC
        LIMIT {query_limit}
    """,
        tuple(args),
        as_dict=True,
    )
    for row in rows:
        row["amount_due"] = _to_float(row["amount_due"])
        row["total_debt_today"] = _to_float(row["total_debt_today"])
        row["urgency"] = "high" if row.get("schedule_status") == "Частично" else "normal"
        row["phone"] = frappe.db.get_value(
            "Customer Phone Number",
            {"parent": row["customer"], "is_primary": 1},
            "phone_number",
        )
    return rows


@frappe.whitelist()
def get_payment_methods():
    """Return the list of valid payment methods from the Payment Transaction Line DocType field.

    Single source of truth for all JS dialogs — avoids hardcoding in pages/reports.
    Excludes 'Комбинированный' (computed, not selectable by the user).
    """
    try:
        options = frappe.get_meta("Payment Transaction Line").get_field("payment_method").options or ""
        methods = [m.strip() for m in options.split("\n") if m.strip() and m.strip() != "Комбинированный"]
        if methods:
            return methods
    except Exception:
        pass
    return ["Наличные", "Наличные USD", "Акксессуар касса", "Наличные UZS",
            "Карта", "Click", "Payme", "Перевод", "Терминал"]


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
    if not customer:
        frappe.throw(_("Клиент обязателен"))

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
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
        ORDER BY isc.modified DESC
        LIMIT {per}
        """,
        (base_td, base_td, *_OPEN_SCHEDULE_STATUSES),
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
    if not installment_plan:
        frappe.throw(_("Укажите план рассрочки"))

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
    overdue_lines = frappe.db.sql(
        f"""
        SELECT COUNT(*)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
    """,
        (base_date, *_OPEN_SCHEDULE_STATUSES),
    )[0][0]

    high_risk_clients = frappe.db.sql(
        f"""
        SELECT COUNT(DISTINCT ip.customer)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE {_COLLECTION_PLAN_WHERE}
          AND (isc.amount - COALESCE(isc.paid_amount, 0)) > 0
          AND (
            isc.status = 'Просрочен'
            OR (isc.due_date < %s AND {open_pred})
          )
          AND DATEDIFF(%s, isc.due_date) > 7
    """,
        (base_date, *_OPEN_SCHEDULE_STATUSES, base_date),
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
def accept_overdue_payment(customer_or_plan=None, amount=None, mode="Наличные"):
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

    from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
        _normalize_payment_line_method,
    )

    customer = frappe.db.get_value("Installment Plan", plan_name, "customer")
    payment = frappe.new_doc("Payment Transaction")
    payment.customer = customer
    payment.status = "Завершен"
    payment.payment_method = mode
    payment.payment_date = nowdate()
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
    frappe.db.commit()
    return {"name": payment.name, "plan": plan_name}


@frappe.whitelist()
def create_sales_order_from_wizard(payload):
    data = frappe.parse_json(payload) if isinstance(payload, str) else payload
    if not data:
        frappe.throw(_("Нет данных для оформления"))

    required_fields = ["customer", "branch", "product", "months"]
    for key in required_fields:
        if not data.get(key):
            frappe.throw(_("Поле {0} обязательно").format(key))

    price = _to_float(data.get("price") or frappe.db.get_value("Product", data["product"], "selling_price"))
    qty = cint(data.get("qty") or 1)
    down_payment = _to_float(data.get("down_payment") or 0)
    interest_rate = _to_float(data.get("interest_rate") or 5)
    months = cint(data.get("months") or 6)
    principal_amount = round(price * qty - down_payment, 2)

    # Use a savepoint so that if Installment Plan creation fails,
    # the Sales Order is also rolled back (no orphaned SO).
    frappe.db.savepoint("wizard_create")
    try:
        so = frappe.new_doc("Sales Order")
        so.customer = data["customer"]
        so.branch = data["branch"]
        so.paid_amount = down_payment if down_payment > 0 else 0
        so.append(
            "items",
            {
                "product": data["product"],
                "quantity": qty,
                "unit_price": price,
                "discount_percent": _to_float(data.get("discount_percent")),
            },
        )
        so.insert(ignore_permissions=True)

        plan = frappe.new_doc("Installment Plan")
        plan.customer = data["customer"]
        plan.branch = data["branch"]
        plan.sales_order = so.name
        plan.principal_amount = principal_amount
        plan.down_payment = down_payment
        plan.number_of_installments = months
        plan.frequency = "Ежемесячно"
        plan.interest_rate = interest_rate
        plan.start_date = today()
        plan.insert(ignore_permissions=True)
    except Exception:
        frappe.db.rollback(save_point="wizard_create")
        raise

    return {"so": so.name, "plan": plan.name}


def _purchase_serial_tracks_consumed(serial_raw, exclude_installment_plan_name=""):
    """
    Serial / IMEI already tied to a sale (SO), warehouse issue (Отпуск), or another open plan (import IMEI only).
    """
    s = (serial_raw or "").strip().replace(" ", "").upper()
    if not s:
        return False
    tail6 = s[-6:] if len(s) >= 6 else s

    if frappe.db.sql(
        """
        SELECT 1 FROM `tabStock Entry Item` oxi
        INNER JOIN `tabStock Entry` ox ON ox.name = oxi.parent
        WHERE ox.docstatus = 1 AND ox.entry_type = 'Отпуск'
          AND NULLIF(TRIM(oxi.serial_no), '') IS NOT NULL
          AND (
            REPLACE(UPPER(TRIM(oxi.serial_no)), ' ', '') = %s
            OR RIGHT(REPLACE(UPPER(TRIM(oxi.serial_no)), ' ', ''), 6) = %s
          )
        LIMIT 1
        """,
        (s, tail6),
    ):
        return True

    if frappe.db.sql(
        """
        SELECT 1 FROM `tabSales Order Item` soi
        INNER JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE so.docstatus = 1
          AND NULLIF(TRIM(soi.imei), '') IS NOT NULL
          AND (
            REPLACE(UPPER(TRIM(soi.imei)), ' ', '') = %s
            OR REPLACE(UPPER(TRIM(soi.imei)), ' ', '') = %s
            OR RIGHT(REPLACE(UPPER(TRIM(soi.imei)), ' ', ''), 6) = %s
          )
        LIMIT 1
        """,
        (s, tail6, tail6),
    ):
        return True

    plan = exclude_installment_plan_name or ""
    if frappe.db.sql(
        """
        SELECT 1 FROM `tabInstallment Plan` ip
        WHERE IFNULL(ip.docstatus, 0) < 2
          AND (%s = '' OR ip.name != %s)
          AND NULLIF(TRIM(ip.imei), '') IS NOT NULL
          AND IFNULL(TRIM(ip.stock_entry), '') = ''
          AND (
            REPLACE(UPPER(TRIM(ip.imei)), ' ', '') = %s
            OR REPLACE(UPPER(TRIM(ip.imei)), ' ', '') = %s
            OR RIGHT(REPLACE(UPPER(TRIM(ip.imei)), ' ', ''), 6) = %s
          )
        LIMIT 1
        """,
        (plan, plan, s, tail6, tail6),
    ):
        return True

    return False


def assert_stock_entry_available_for_installment_plan(stock_entry, current_plan_name=None):
    """
    Submitted Поступление only; positive stock in ledger; not marked sold/return;
    serial not already sold (SO / Отпуск / other plan IMEI); not linked to another open plan.
    """
    if not stock_entry:
        return
    row = frappe.db.get_value(
        "Stock Entry",
        stock_entry,
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
    if bs in _STOCK_ENTRY_UNAVAILABLE_BUSINESS:
        frappe.throw(
            _("Это поступление уже отражено как продажа или возврат. Выберите другой документ.")
        )
    plan = current_plan_name or ""
    conflict = frappe.db.sql(
        """
        SELECT name FROM `tabInstallment Plan`
        WHERE stock_entry = %s
          AND docstatus < 2
          AND name != %s
        LIMIT 1
        """,
        (stock_entry, plan),
    )
    if conflict:
        frappe.throw(
            _("Этот документ поступления уже привязан к плану {0}.").format(conflict[0][0])
        )

    sei = frappe.db.get_value(
        "Stock Entry Item",
        {"parent": stock_entry, "idx": 1},
        ["product", "serial_no"],
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

    if sei and sei.serial_no and str(sei.serial_no).strip():
        if _purchase_serial_tracks_consumed(sei.serial_no, plan):
            frappe.throw(
                _(
                    "Этот серийный номер уже в продаже (заказ), отпуске или другом плане рассрочки. Выберите другое поступление."
                )
            )


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
    """Link search: only free Поступление (not sold / not used on another plan)."""
    if isinstance(filters, str):
        filters = json.loads(filters) if filters else {}
    plan_name = (filters or {}).get("installment_plan") or ""

    start, page_length = cint(start), cint(page_length)

    conditions = [
        "se.docstatus = 1",
        "se.entry_type = %s",
        """IFNULL(NULLIF(TRIM(se.business_status), ''), 'Проведен') NOT IN (
            'Naqd savdo', 'Rassrochka savdo', 'Возврат'
        )""",
        """NOT EXISTS (
            SELECT 1 FROM `tabInstallment Plan` ip
            WHERE ip.stock_entry = se.name
              AND IFNULL(ip.docstatus, 0) < 2
              AND (%s = '' OR ip.name != %s)
        )""",
        """IFNULL((
            SELECT SUM(sl.quantity_change) FROM `tabStock Ledger` sl
            WHERE sl.product = sei.product AND sl.warehouse = se.warehouse
        ), 0) > 0""",
        """NOT (
            NULLIF(TRIM(sei.serial_no), '') IS NOT NULL
            AND (
                EXISTS (
                    SELECT 1 FROM `tabStock Entry Item` oxi
                    INNER JOIN `tabStock Entry` ox ON ox.name = oxi.parent
                    WHERE ox.docstatus = 1 AND ox.entry_type = 'Отпуск'
                      AND NULLIF(TRIM(oxi.serial_no), '') IS NOT NULL
                      AND (
                        REPLACE(UPPER(TRIM(oxi.serial_no)), ' ', '') = REPLACE(UPPER(TRIM(sei.serial_no)), ' ', '')
                        OR RIGHT(REPLACE(UPPER(TRIM(oxi.serial_no)), ' ', ''), 6)
                         = RIGHT(REPLACE(UPPER(TRIM(sei.serial_no)), ' ', ''), 6)
                      )
                )
                OR EXISTS (
                    SELECT 1 FROM `tabSales Order Item` soi
                    INNER JOIN `tabSales Order` so_order ON so_order.name = soi.parent
                    WHERE so_order.docstatus = 1
                      AND NULLIF(TRIM(soi.imei), '') IS NOT NULL
                      AND (
                        REPLACE(UPPER(TRIM(soi.imei)), ' ', '') = REPLACE(UPPER(TRIM(sei.serial_no)), ' ', '')
                        OR REPLACE(UPPER(TRIM(soi.imei)), ' ', '')
                         = RIGHT(REPLACE(UPPER(TRIM(sei.serial_no)), ' ', ''), 6)
                        OR RIGHT(REPLACE(UPPER(TRIM(soi.imei)), ' ', ''), 6)
                         = RIGHT(REPLACE(UPPER(TRIM(sei.serial_no)), ' ', ''), 6)
                      )
                )
                OR EXISTS (
                    SELECT 1 FROM `tabInstallment Plan` ip2
                    WHERE IFNULL(ip2.docstatus, 0) < 2
                      AND (%s = '' OR ip2.name != %s)
                      AND NULLIF(TRIM(ip2.imei), '') IS NOT NULL
                      AND IFNULL(TRIM(ip2.stock_entry), '') = ''
                      AND (
                        REPLACE(UPPER(TRIM(ip2.imei)), ' ', '') = REPLACE(UPPER(TRIM(sei.serial_no)), ' ', '')
                        OR REPLACE(UPPER(TRIM(ip2.imei)), ' ', '')
                         = RIGHT(REPLACE(UPPER(TRIM(sei.serial_no)), ' ', ''), 6)
                        OR RIGHT(REPLACE(UPPER(TRIM(ip2.imei)), ' ', ''), 6)
                         = RIGHT(REPLACE(UPPER(TRIM(sei.serial_no)), ' ', ''), 6)
                      )
                )
            )
        )""",
    ]
    args = ["Поступление", plan_name, plan_name, plan_name, plan_name]

    if txt:
        conditions.append("(se.name LIKE %s OR se.items_summary LIKE %s)")
        args.extend([f"%{txt}%", f"%{txt}%"])

    where_sql = " AND ".join(conditions)
    args.extend([page_length, start])

    return frappe.db.sql(
        f"""
        SELECT se.name, IFNULL(NULLIF(TRIM(se.items_summary), ''), se.name)
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Item` sei ON sei.parent = se.name AND sei.idx = 1
        WHERE {where_sql}
        ORDER BY se.modified DESC
        LIMIT %s OFFSET %s
        """,
        tuple(args),
        as_list=1,
    )


@frappe.whitelist()
def get_stock_entry_details_for_installment_plan(stock_entry, installment_plan=None):
    """
    Installment Plan form: auto-fill principal / product / serial from a submitted receipt.
    Implemented under api.* (not doctype controller) so desk RPC always resolves the method.
    """
    if not stock_entry:
        return {}
    assert_stock_entry_available_for_installment_plan(stock_entry, installment_plan or "")
    ste = frappe.get_doc("Stock Entry", stock_entry)
    total = flt(ste.total_value)
    if not total and ste.items:
        total = sum(flt(i.amount) for i in ste.items)
    result = {
        "total_amount": total,
        "customer": None,
        "product_name": None,
        "imei": None,
    }
    if ste.items:
        first = ste.items[0]
        if first.product:
            result["product_name"] = frappe.db.get_value(
                "Product", first.product, "product_name"
            )
        result["imei"] = first.serial_no
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
