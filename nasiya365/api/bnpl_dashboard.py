import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, get_last_day, getdate, nowdate, today


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


def _trend_payload(current, previous):
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

    due_today = frappe.db.sql(
        """
        SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE ip.docstatus = 1
          AND isc.status IN ('Ожидает', 'Частично')
          AND isc.due_date = %s
    """,
        (base_date,),
    )[0][0]

    overdue = frappe.db.sql(
        """
        SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE ip.docstatus = 1
          AND isc.status = 'Просрочен'
    """,
        (),
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
def get_overdue_list(limit=20, branch=None, collector=None):
    query_limit = _safe_limit(limit, default_value=20, max_value=200)
    args = [getdate(today())]
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
            cp.full_name AS customer_name,
            isc.name AS schedule_name,
            isc.due_date,
            (isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due,
            DATEDIFF(%s, isc.due_date) AS days_overdue
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE ip.docstatus = 1
          AND isc.status = 'Просрочен'
          {collector_clause}
        ORDER BY days_overdue DESC, amount_due DESC
        LIMIT {query_limit}
    """,
        tuple(args),
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
    args = [getdate(today())]

    rows = frappe.db.sql(
        f"""
        SELECT
            ip.name AS installment_plan,
            ip.customer,
            cp.full_name AS customer_name,
            isc.name AS schedule_name,
            isc.due_date,
            isc.status AS schedule_status,
            (isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE ip.docstatus = 1
          AND isc.status IN ('Ожидает', 'Частично')
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
        row["urgency"] = "high" if row.get("schedule_status") == "Частично" else "normal"
    return rows


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
    return {
        "customer": doc.name,
        "full_name": doc.full_name,
        "primary_phone": primary_phone,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "active_loans": active_plans,
        "overdue_loans": overdue_count,
        "total_debt": _to_float(doc.total_debt),
        "available_limit": _to_float(doc.available_limit),
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
    missed = frappe.db.sql(
        f"""
        SELECT
            isc.name AS schedule_ref,
            isc.modified,
            isc.due_date,
            ip.name AS installment_plan,
            ip.customer,
            cp.full_name AS customer_name,
            (isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due,
            DATEDIFF(%s, isc.due_date) AS days_overdue
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE ip.docstatus = 1
          AND isc.status = 'Просрочен'
        ORDER BY isc.modified DESC
        LIMIT {per}
        """,
        (getdate(today()),),
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
        amt = _to_float(
            frappe.db.sql(
                """
                SELECT COALESCE(SUM(isc.amount - COALESCE(isc.paid_amount, 0)), 0)
                FROM `tabInstallment Schedule` isc
                WHERE isc.parent = %s
                  AND isc.status IN ('Ожидает', 'Частично')
                  AND isc.due_date = %s
                """,
                (installment_plan, getdate(today())),
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

    overdue_lines = frappe.db.sql(
        """
        SELECT COUNT(*)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE ip.docstatus = 1
          AND isc.status = 'Просрочен'
    """,
        (),
    )[0][0]

    high_risk_clients = frappe.db.sql(
        """
        SELECT COUNT(DISTINCT ip.customer)
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        WHERE ip.docstatus = 1
          AND isc.status = 'Просрочен'
          AND DATEDIFF(%s, isc.due_date) > 7
    """,
        (base_date,),
    )[0][0]

    due_today = cur["due_today_amount"]
    collected = cur["cash_collected_today"]
    if due_today > 0:
        collection_progress_pct = min(100.0, round((collected / due_today) * 100, 1))
    else:
        collection_progress_pct = 100.0 if collected > 0 else 0.0

    top_overdue = get_overdue_list(limit=5)

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

    customer = frappe.db.get_value("Installment Plan", plan_name, "customer")
    payment = frappe.new_doc("Payment Transaction")
    payment.customer = customer
    payment.amount = amount
    payment.status = "Завершен"
    payment.payment_method = mode
    payment.payment_date = nowdate()
    payment.reference_doctype = "Installment Plan"
    payment.reference_name = plan_name
    payment.received_by = frappe.session.user
    payment.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": payment.name, "plan": plan_name}


@frappe.whitelist()
def create_sales_order_from_wizard(payload):
    data = frappe.parse_json(payload) if isinstance(payload, str) else payload
    if not data:
        frappe.throw(_("Нет данных для оформления"))

    required_fields = ["customer", "branch", "product", "sale_type", "months"]
    for key in required_fields:
        if not data.get(key):
            frappe.throw(_("Поле {0} обязательно").format(key))

    price = _to_float(data.get("price") or frappe.db.get_value("Product", data["product"], "selling_price"))
    qty = cint(data.get("qty") or 1)
    down_payment = _to_float(data.get("down_payment") or 0)
    total_amount = price * qty

    so = frappe.new_doc("Sales Order")
    so.customer = data["customer"]
    so.branch = data["branch"]
    so.sale_type = data["sale_type"]
    so.paid_amount = down_payment if data["sale_type"] in ("Рассрочка", "Смешанный") else total_amount
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
    return {"name": so.name, "docstatus": so.docstatus}
