"""
Payment Transaction DocType Controller
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

# Child table "Payment Transaction Line" Select options (must match doctype JSON)
_PAYMENT_LINE_METHODS = frozenset(
    (
        "Наличные",
        "Наличные USD",
        "Акксессуар касса",
        "Наличные UZS",
        "Карта",
        "Click",
        "Payme",
        "Перевод",
        "Терминал",
    )
)


def _normalize_payment_line_method(mode):
    m = (mode or "").strip()
    return m if m in _PAYMENT_LINE_METHODS else "Наличные USD"


def _installment_plan_has_outstanding_payment(plan_name: str) -> bool:
    """True if header shows debt or any schedule row still has amount due (header can be wrong/NULL)."""
    if not plan_name:
        return False
    rb = frappe.db.get_value("Installment Plan", plan_name, "remaining_balance")
    if flt(rb) > 0:
        return True
    due = frappe.db.sql(
        """
        SELECT COALESCE(SUM(COALESCE(amount, 0) - COALESCE(paid_amount, 0)), 0)
        FROM `tabInstallment Schedule`
        WHERE parent = %s
        """,
        (plan_name,),
    )[0][0]
    return flt(due) > 0.0001


def installment_plans_with_outstanding_for_customer(customer):
    """Active/overdue plans for this customer that still have something to collect (by header or график)."""
    if not customer:
        return []
    names = frappe.get_all(
        "Installment Plan",
        filters={"customer": customer, "status": ["in", ["Активный", "Просрочен"]]},
        pluck="name",
        order_by="modified desc",
    )
    return [n for n in names if _installment_plan_has_outstanding_payment(n)]


def single_open_installment_plan_for_customer(customer):
    """
    If the customer has exactly one active/overdue plan with outstanding debt (header or schedule), return its name.
    Used to auto-link payments when the cashier did not press «Выбрать».
    """
    eligible = installment_plans_with_outstanding_for_customer(customer)
    if len(eligible) == 1:
        return eligible[0]
    return None


def allocate_payment_transaction_to_installment_plan(doc):
    """Apply this payment amount to the linked Installment Plan schedule (idempotent per successful run)."""
    if getattr(doc, "_nasiya_installment_plan_allocated", False):
        return
    rd = (doc.reference_doctype or "").strip()
    rn = (doc.reference_name or "").strip()
    if rd != "Installment Plan" or not rn:
        return
    amt = flt(doc.amount)
    if amt <= 0:
        return
    plan = frappe.get_doc("Installment Plan", rn)
    try:
        plan.apply_payment(amt, payment_transaction=doc.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Payment Transaction: allocate to Installment Plan")
        raise
    doc._nasiya_installment_plan_allocated = True


class PaymentTransaction(Document):
    def validate(self):
        self.autolink_single_open_installment_plan()
        self.apply_payment_totals()

    def before_insert(self):
        if not self.received_by:
            self.received_by = frappe.session.user

    def after_insert(self):
        """Plan allocation runs via hooks.py doc_events (payment_doc_events)."""
        if self.get("send_payment_sms"):
            try:
                _send_payment_receipt_sms(self)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Payment Transaction SMS")

    def autolink_single_open_installment_plan(self):
        """Set Installment Plan reference when both ref fields are empty and the client has only one open plan with debt."""
        rd = (self.reference_doctype or "").strip()
        rn = (self.reference_name or "").strip()
        if rd == "Installment Plan" and rn:
            return
        if rd or rn:
            return
        if not self.customer:
            return
        plan = single_open_installment_plan_for_customer(self.customer)
        if not plan:
            return
        self.reference_doctype = "Installment Plan"
        self.reference_name = plan

    def apply_payment_totals(self):
        """Calculate totals only from payment_lines table rows."""
        return self._apply_table_payment_totals()

    def _apply_table_payment_totals(self):
        if not self.payment_lines:
            frappe.throw(_("Добавьте хотя бы одну строку в детали оплаты"))

        total = 0.0
        methods = set()
        default_rate = flt(self.exchange_rate)

        for row in self.payment_lines:
            row_amount = flt(row.amount)
            if row_amount <= 0:
                continue

            row_currency = (row.currency or "USD").strip().upper()
            if row_currency == "UZS":
                rate = flt(row.exchange_rate or default_rate)
                if rate <= 0:
                    frappe.throw(
                        _("Укажите корректный курс USD для строки {0} (UZS)").format(row.idx)
                    )
                total += row_amount / rate
            else:
                total += row_amount

            if row.payment_method:
                methods.add(row.payment_method.strip())

        if total <= 0:
            frappe.throw(_("Сумма по строкам оплаты должна быть больше нуля"))

        self.amount = total
        if len(methods) > 1:
            self.payment_method = "Комбинированный"
        elif len(methods) == 1:
            self.payment_method = list(methods)[0]

def _send_payment_receipt_sms(doc):
    if not doc.customer:
        return
    cust = frappe.get_doc("Customer Profile", doc.customer)
    phone = cust.get_primary_phone()
    if not phone:
        return

    from frappe.utils import fmt_money

    from nasiya365.utils.sms_manager import SMSManager

    message = _("Оплата {0} принята. Документ {1}.").format(fmt_money(doc.amount), doc.name)
    SMSManager().send_sms(phone, message)


@frappe.whitelist()
def get_customer_installment_plans(customer):
    """Return all installment plans for a customer with debt + device info."""
    if not customer:
        return []

    rows = frappe.db.sql(
        """
        SELECT
            ip.name,
            ip.status,
            ip.contract_status,
            ip.remaining_balance,
            ip.total_amount,
            ip.installment_amount,
            ip.sales_order,
            ip.stock_entry,
            ip.imei,
            ip.contract_number,
            COALESCE(NULLIF(TRIM(CONCAT_WS(' · ',
                NULLIF(TRIM(COALESCE(
                    NULLIF(ip.product_name, ''),
                    NULLIF(p.product_name, ''),
                    NULLIF(soi.product_name, ''),
                    ''
                )), ''),
                NULLIF(TRIM(COALESCE(NULLIF(sei.color, ''), NULLIF(soi.color, ''), '')), ''),
                NULLIF(TRIM(COALESCE(NULLIF(sei.storage, ''), NULLIF(soi.storage, ''), '')), '')
            )), ''), '') AS device_name
        FROM `tabInstallment Plan` ip
        LEFT JOIN `tabStock Entry Item` sei
            ON sei.parent = ip.stock_entry AND sei.idx = 1
        LEFT JOIN `tabProduct` p
            ON p.name = sei.product
        LEFT JOIN `tabSales Order Item` soi
            ON soi.parent = ip.sales_order AND soi.idx = 1
        WHERE ip.customer = %s
        ORDER BY ip.modified DESC
        """,
        (customer,),
        as_dict=True,
    )
    return rows or []


@frappe.whitelist()
def backfill_unlinked_payments_for_customer(customer):
    """
    Link existing payments that have no reference document to the customer's only open plan (with debt),
    then save each so allocation runs. Use once to repair old rows.
    """
    if not customer:
        frappe.throw(_("Укажите клиента"))
    if not frappe.has_permission("Customer Profile", "read", customer):
        frappe.throw(_("Нет доступа к этому клиенту"))
    if not frappe.has_permission("Payment Transaction", "write"):
        frappe.throw(_("Нет права изменять платежи"))

    plan = single_open_installment_plan_for_customer(customer)
    if not plan:
        n_eligible = len(installment_plans_with_outstanding_for_customer(customer))
        frappe.throw(
            _("Автопривязка невозможна: подходящих планов с непогашенным графиком — {0}. Укажите план вручную («Выбрать»).").format(
                n_eligible
            )
        )

    rows = frappe.get_all(
        "Payment Transaction",
        filters={"customer": customer, "amount": [">", 0]},
        fields=["name", "reference_doctype", "reference_name"],
        order_by="payment_date asc, creation asc",
    )
    updated = []
    for row in rows:
        rd = (row.reference_doctype or "").strip()
        rn = (row.reference_name or "").strip()
        if rd == "Installment Plan" and rn:
            continue
        if rd or rn:
            continue
        doc = frappe.get_doc("Payment Transaction", row.name)
        doc.reference_doctype = "Installment Plan"
        doc.reference_name = plan
        doc.save(ignore_permissions=True)

        updated.append(doc.name)

    return {"plan": plan, "updated_payments": updated, "count": len(updated)}
