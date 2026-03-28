import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate, today


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


@frappe.whitelist()
def get_bnpl_kpis(date=None, branch=None):
    base_date = getdate(date) if date else getdate(today())

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

    return {
        "outstanding_amount": _to_float(outstanding),
        "due_today_amount": _to_float(due_today),
        "overdue_amount": _to_float(overdue),
        "cash_collected_today": _to_float(collected_today),
        "filters": {"date": str(base_date), "branch": branch},
    }


@frappe.whitelist()
def get_overdue_list(limit=20, branch=None, collector=None):
    sql_filters = []
    args = [getdate(today())]
    if collector:
        sql_filters.append("pt.collected_by = %s")
        args.append(collector)

    extra_filter = ""
    if sql_filters:
        extra_filter = " AND " + " AND ".join(sql_filters)

    query_limit = _safe_limit(limit, default_value=20, max_value=200)

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
        LEFT JOIN `tabPayment Transaction` pt ON pt.reference_name = ip.name
        WHERE ip.docstatus = 1
          AND isc.status = 'Просрочен'
          {extra_filter}
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
            isc.due_date,
            (isc.amount - COALESCE(isc.paid_amount, 0)) AS amount_due
        FROM `tabInstallment Schedule` isc
        INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
        INNER JOIN `tabCustomer Profile` cp ON cp.name = ip.customer
        WHERE ip.docstatus = 1
          AND isc.status IN ('Ожидает', 'Частично')
          AND isc.due_date = %s
          {filters}
        ORDER BY amount_due DESC
        LIMIT {query_limit}
    """,
        tuple(args),
        as_dict=True,
    )
    for row in rows:
        row["amount_due"] = _to_float(row["amount_due"])
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
    return {
        "customer": doc.name,
        "full_name": doc.full_name,
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
