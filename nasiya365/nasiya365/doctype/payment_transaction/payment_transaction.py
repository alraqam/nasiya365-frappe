"""
Payment Transaction DocType Controller
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

_FALLBACK_METHODS = frozenset((
    "Наличные USD", "Акксессуар касса", "Наличные UZS",
    "Карта", "Click", "Payme", "Перевод", "Терминал",
))

# Legacy method names that must be mapped to a canonical value before storing.
_METHOD_ALIASES = {
    "Наличные": "Наличные USD",
}

# Methods that are never valid to store (removed from options but may exist in old data).
_EXCLUDED_METHODS = frozenset(("Наличные",))


def _get_payment_line_methods() -> frozenset:
    """Return valid payment methods. Priority: Merchant Settings → DocType meta → fallback."""
    # 1. Merchant Settings (admin-editable, no migration needed)
    try:
        raw = frappe.db.get_single_value("Merchant Settings", "payment_methods") or ""
        methods = frozenset(
            m.strip() for m in raw.splitlines()
            if m.strip() and m.strip() not in ("Комбинированный",) | _EXCLUDED_METHODS
        )
        if methods:
            return methods
    except Exception:
        pass
    # 2. DocType meta
    try:
        options = frappe.get_meta("Payment Transaction Line").get_field("payment_method").options or ""
        methods = frozenset(
            m.strip() for m in options.split("\n")
            if m.strip() and m.strip() not in ("Комбинированный",) | _EXCLUDED_METHODS
        )
        if methods:
            return methods
    except Exception:
        pass
    return _FALLBACK_METHODS


def _normalize_payment_line_method(mode):
    m = (mode or "").strip()
    m = _METHOD_ALIASES.get(m, m)  # resolve legacy aliases first
    return m if m in _get_payment_line_methods() else "Наличные USD"


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
    """Plans that are not closed/write-off and still have something to collect (by header or график).

    Branch-scoped: a cashier in Branch A cannot autolink to a plan in Branch B,
    so payments stay within branch boundaries.
    """
    if not customer:
        return []
    from nasiya365.api.bnpl_dashboard import _user_branch_clause

    branch_clause, branch_params = _user_branch_clause("ip")
    names = frappe.db.sql(
        f"""
        SELECT ip.name FROM `tabInstallment Plan` ip
        WHERE ip.customer = %s
          AND IFNULL(ip.`status`, '') NOT IN ('Завершен', 'Списан')
          {branch_clause}
        ORDER BY ip.modified DESC
        """,
        (customer, *branch_params),
        pluck=True,
    )
    names = names or []
    return [n for n in names if _installment_plan_has_outstanding_payment(n)]


def _get_installment_plan_for_payment(plan_name: str):
    """Load plan for allocation; works across Frappe versions and permission setups."""
    try:
        return frappe.get_doc("Installment Plan", plan_name, ignore_permissions=True)
    except TypeError:
        pass
    prev = frappe.flags.get("ignore_permissions")
    frappe.flags.ignore_permissions = True
    try:
        return frappe.get_doc("Installment Plan", plan_name)
    finally:
        if prev is None:
            frappe.flags.ignore_permissions = False
        else:
            frappe.flags.ignore_permissions = prev


def single_open_installment_plan_for_customer(customer):
    """
    If the customer has exactly one non-closed plan with outstanding debt (header or schedule), return its name.
    Used to auto-link payments when the cashier did not press «Выбрать».
    """
    eligible = installment_plans_with_outstanding_for_customer(customer)
    if len(eligible) == 1:
        return eligible[0]
    return None


def _merge_payment_allocation_fields_from_db(doc):
    """Ensure reference + amount match DB after insert (avoids empty dynamic link on the in-memory doc)."""
    name = getattr(doc, "name", None)
    if not name or str(name).startswith("new-"):
        return
    if not frappe.db.exists("Payment Transaction", name):
        return
    row = frappe.db.get_value(
        "Payment Transaction",
        name,
        ["reference_doctype", "reference_name", "amount"],
        as_dict=True,
    )
    if not row:
        return
    if row.get("reference_doctype"):
        doc.reference_doctype = row.reference_doctype
    if row.get("reference_name"):
        doc.reference_name = row.reference_name
    if row.get("amount") is not None:
        doc.amount = row.amount


def allocate_payment_transaction_to_installment_plan(doc):
    """Apply this payment amount to the linked Installment Plan schedule (idempotent per successful run)."""
    if getattr(doc, "_nasiya_installment_plan_allocated", False):
        return
    _merge_payment_allocation_fields_from_db(doc)
    rd = (doc.reference_doctype or "").strip()
    rn = (doc.reference_name or "").strip()

    plan_name = None
    if rd == "Installment Plan" and rn:
        plan_name = rn
    elif rd == "Sales Order" and rn:
        # Legacy/import/payment-from-SO flows: map SO -> linked installment plan.
        plan_name = frappe.db.get_value("Installment Plan", {"sales_order": rn}, "name")
    if not plan_name:
        return

    amt = flt(doc.amount)
    if amt <= 0:
        return

    # F4: re-verify header amount matches payment_lines total at allocation time,
    # so direct `db.set_value` or stale in-memory state can't desync cashbox vs plan.
    if doc.name:
        line_total_usd = _recompute_payment_lines_total_usd(doc)
        if line_total_usd > 0 and abs(line_total_usd - amt) > 0.01:
            frappe.log_error(
                f"Payment {doc.name}: header amount {amt:.4f} != payment_lines total {line_total_usd:.4f}",
                "Payment Transaction: amount mismatch",
            )
            frappe.throw(
                _("Сумма платежа ({0}) не совпадает с суммой строк оплаты ({1}). Пересохраните документ.").format(
                    f"{amt:.2f}", f"{line_total_usd:.2f}"
                )
            )

    # D6: Hard idempotency with row lock. SELECT FOR UPDATE serializes concurrent
    # allocations of the same payment; the second caller will see the row once the
    # first commits and short-circuit instead of double-applying.
    if doc.name:
        locked = frappe.db.sql(
            """
            SELECT name FROM `tabInstallment Schedule`
            WHERE parent = %s AND payment_transaction = %s
            FOR UPDATE
            """,
            (plan_name, doc.name),
        )
        if locked:
            doc._nasiya_installment_plan_allocated = True
            return

    plan = _get_installment_plan_for_payment(plan_name)
    try:
        excess = plan.apply_payment(amt, payment_transaction=doc.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Payment Transaction: allocate to Installment Plan")
        raise

    if flt(excess) > 0.001:
        frappe.db.set_value(
            "Payment Transaction", doc.name, "overpayment_amount", flt(excess),
            update_modified=False,
        )
        doc.overpayment_amount = flt(excess)
        # F5: surface overpayment visibly — cashier needs to know they accepted extra money.
        frappe.msgprint(
            _("Переплата {0} USD по плану {1}. Отразите как возврат или кредит клиенту.").format(
                f"{flt(excess):.2f}", plan_name
            ),
            indicator="orange",
            alert=True,
        )
        frappe.log_error(
            f"Payment {doc.name} has excess of {excess:.4f} USD over plan {plan_name}. "
            f"Payment amount: {amt}, plan remaining_balance was: {flt(amt) - flt(excess):.4f}",
            "Payment Transaction: Overpayment",
        )

    doc._nasiya_installment_plan_allocated = True


def _recompute_payment_lines_total_usd(doc) -> float:
    """Recompute USD total from stored payment_lines rows (DB-authoritative).

    Returns 0 if the doc has no persisted lines yet (e.g. during initial insert).
    """
    if not getattr(doc, "name", None):
        return 0.0
    default_rate = flt(getattr(doc, "exchange_rate", 0))
    rows = frappe.get_all(
        "Payment Transaction Line",
        filters={"parent": doc.name, "parenttype": "Payment Transaction"},
        fields=["amount", "currency", "exchange_rate"],
    )
    if not rows:
        return 0.0
    total = 0.0
    for r in rows:
        row_amount = flt(r.amount)
        if row_amount <= 0:
            continue
        currency = (r.currency or "USD").strip().upper()
        if currency == "UZS":
            rate = flt(r.exchange_rate or default_rate)
            if rate <= 0:
                return 0.0  # cannot compute — skip check
            total += row_amount / rate
        else:
            total += row_amount
    return total


def _get_open_cashbox_for_payment(doc):
    """Pick an open cashbox for the receiver (fallback: any latest open)."""
    receiver = (getattr(doc, "received_by", None) or frappe.session.user or "").strip()
    if receiver:
        preferred = frappe.db.get_value(
            "Cashbox",
            {"status": "Открыта", "responsible_user": receiver},
            "name",
            order_by="opening_date desc, modified desc",
        )
        if preferred:
            return preferred
    return frappe.db.get_value(
        "Cashbox",
        {"status": "Открыта"},
        "name",
        order_by="opening_date desc, modified desc",
    )


def _is_down_payment_capture(doc) -> bool:
    """
    Treat first payments on Installment Plan as down payment capture when plan has down_payment.
    Works even if client pays the initial contribution later.
    """
    if (doc.reference_doctype or "").strip() != "Installment Plan":
        return False
    plan_name = (doc.reference_name or "").strip()
    if not plan_name:
        return False
    down_payment = flt(frappe.db.get_value("Installment Plan", plan_name, "down_payment"))
    if down_payment <= 0:
        return False
    prior_total = frappe.db.sql(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM `tabPayment Transaction`
        WHERE docstatus < 2
          AND status = 'Завершен'
          AND reference_doctype = 'Installment Plan'
          AND reference_name = %s
          AND name != %s
          AND creation <= %s
        """,
        (plan_name, doc.name, doc.creation),
    )[0][0]
    return flt(prior_total) < down_payment


def _sync_payment_to_cashbox(doc):
    """Write payment lines to cashbox by payment type (idempotent, concurrent-safe)."""
    if getattr(doc, "_nasiya_cashbox_synced", False):
        return
    if (doc.status or "").strip() != "Завершен":
        return
    if not doc.payment_lines:
        return

    cashbox_name = _get_open_cashbox_for_payment(doc)
    if not cashbox_name:
        frappe.log_error("Open cashbox not found", f"Payment Transaction cashbox sync: {doc.name}")
        return

    # Hard DB-level idempotency check — rows are saved atomically so partial states don't occur.
    # This catches concurrent workers that both passed the in-request flag check.
    already_synced = frappe.db.sql(
        """SELECT 1 FROM `tabCashbox Transaction`
           WHERE parent = %s
             AND reference_doctype = 'Payment Transaction'
             AND reference_name = %s
           LIMIT 1""",
        (cashbox_name, doc.name),
    )
    if already_synced:
        doc._nasiya_cashbox_synced = True
        return

    # Build the rows to append before touching the cashbox doc
    is_initial = _is_down_payment_capture(doc)
    rows_to_add = []
    for row in doc.payment_lines:
        line_amount = flt(row.amount)
        if line_amount <= 0:
            continue
        line_method = (row.payment_method or "").strip() or "Наличные USD"
        line_currency = (row.currency or "USD").strip().upper()
        line_rate = flt(getattr(row, "exchange_rate", 0))
        marker = f"[PT:{doc.name}|ROW:{row.idx}]"
        purpose = "Первоначальный взнос" if is_initial else "Платеж по рассрочке"
        rows_to_add.append({
            "transaction_type": "Приход",
            "payment_method": line_method,
            "currency": line_currency,
            "exchange_rate": line_rate,
            "amount": line_amount,
            "category": "Оплата получена",
            "reference_doctype": "Payment Transaction",
            "reference_name": doc.name,
            "notes": f"{purpose} · {line_method} · {line_currency} {marker}",
        })

    if not rows_to_add:
        doc._nasiya_cashbox_synced = True
        return

    # Reload cashbox fresh right before appending to minimise the stale-data window.
    # Then do a second in-memory check on the reloaded version before writing.
    cashbox = frappe.get_doc("Cashbox", cashbox_name)
    already_in_reload = any(
        t.reference_doctype == "Payment Transaction" and t.reference_name == doc.name
        for t in (cashbox.transactions or [])
    )
    if already_in_reload:
        doc._nasiya_cashbox_synced = True
        return

    for t in (cashbox.transactions or []):
        t.payment_method = _normalize_payment_line_method(t.payment_method)

    for row_data in rows_to_add:
        cashbox.append("transactions", row_data)
    cashbox.save(ignore_permissions=True)
    doc._nasiya_cashbox_synced = True


def _deallocate_payment_from_installment_plan(doc):
    """Reverse schedule-row updates made by a payment transaction on cancel.

    Finds every Installment Schedule row referencing this payment, reduces its
    paid_amount by the allocated share, resets status if fully unpaid, then
    recalculates plan totals.
    """
    if not getattr(doc, "name", None):
        return

    rows = frappe.db.sql(
        """
        SELECT isc.name, isc.parent, isc.amount, isc.paid_amount, isc.status
        FROM `tabInstallment Schedule` isc
        WHERE isc.payment_transaction = %s
        """,
        (doc.name,),
        as_dict=True,
    )
    if not rows:
        return

    # Reverse by the smaller of (this row's paid_amount, total doc amount spread proportionally).
    # Since the original allocation wrote payment_transaction on rows it paid/partially paid,
    # we simply clear paid_amount back to zero on those rows (the transaction is the sole
    # contributor — Frappe stores at most one payment_transaction reference per row).
    affected_plans = set()
    for r in rows:
        frappe.db.set_value(
            "Installment Schedule",
            r.name,
            {
                "paid_amount": 0,
                "paid_date": None,
                "payment_transaction": None,
                "status": "Ожидает",
            },
            update_modified=False,
        )
        affected_plans.add(r.parent)

    for plan_name in affected_plans:
        # When the plan itself is being cancelled, its before_cancel hook drives the PT
        # cascade. Schedule rows are already zeroed atomically (db.set_value above); skip the
        # plan.save() here so we don't fight Frappe's pending docstatus→2 db_update.
        if frappe.flags.get("nasiya_plan_cancel_cascade"):
            continue
        try:
            plan = _get_installment_plan_for_payment(plan_name)
        except Exception:
            continue
        paid_total = sum(flt(s.paid_amount) for s in plan.schedule)
        plan.paid_amount = paid_total
        plan.remaining_balance = flt(plan.total_amount) - paid_total
        # Unwind terminal status so future payments can apply again.
        if plan.status == "Завершен":
            plan.status = "Активный" if cint(plan.docstatus) == 1 else "Черновик"
        plan.flags.ignore_validate_update_after_submit = True
        frappe.flags.nasiya_plan_allocating_payment = True
        try:
            plan.save(ignore_permissions=True)
        finally:
            frappe.flags.nasiya_plan_allocating_payment = False


def _remove_payment_from_cashbox(doc):
    """Remove all cashbox rows created for this payment."""
    if not getattr(doc, "name", None):
        return
    cashboxes = frappe.get_all(
        "Cashbox",
        filters={"status": ["in", ["Открыта", "Закрыта"]]},
        pluck="name",
    )
    if not cashboxes:
        return
    changed = False
    for cashbox_name in cashboxes:
        cashbox = frappe.get_doc("Cashbox", cashbox_name)
        before = len(cashbox.transactions or [])
        cashbox.transactions = [
            t
            for t in (cashbox.transactions or [])
            if not (
                (t.reference_doctype == "Payment Transaction")
                and (t.reference_name == doc.name)
            )
        ]
        if len(cashbox.transactions or []) != before:
            cashbox.save(ignore_permissions=True)
            changed = True
    if changed:
        doc._nasiya_cashbox_synced = False


class PaymentTransaction(Document):
    """Payment Transaction state machine:

        docstatus=0, status=Ожидает    → draft, not yet posted to plan/cashbox
        docstatus=1, status=Завершен    → posted: plan schedule rows allocated, cashbox row added
        docstatus=2, status=Отменен     → cancelled: allocation + cashbox row reversed

    Side-effects on transitions:
        on_submit  → status=Завершен, allocate_payment_transaction_to_installment_plan,
                     _sync_payment_to_cashbox, optional SMS receipt
        on_cancel  → status=Отменен, _deallocate_payment_from_installment_plan,
                     _remove_payment_from_cashbox, audit comment
    """

    def validate(self):
        self.autolink_single_open_installment_plan()
        self.apply_payment_totals()

    def before_insert(self):
        if not self.received_by:
            self.received_by = frappe.session.user

    def on_submit(self):
        """Submission posts the payment: allocate to plan, sync cashbox, send SMS."""
        self.db_set("status", "Завершен", update_modified=False)
        frappe.db.savepoint("nasiya_payment_on_submit")
        try:
            allocate_payment_transaction_to_installment_plan(self)
            _sync_payment_to_cashbox(self)
        except Exception:
            frappe.db.rollback(save_point="nasiya_payment_on_submit")
            raise
        if self.get("send_payment_sms"):
            try:
                _send_payment_receipt_sms(self)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Payment Transaction SMS")
                frappe.msgprint(
                    _("SMS-уведомление не отправлено. Платёж сохранён, но клиент не получил сообщение."),
                    indicator="orange",
                    alert=True,
                )

    def on_cancel(self):
        self.db_set("status", "Отменен", update_modified=False)
        _deallocate_payment_from_installment_plan(self)
        _remove_payment_from_cashbox(self)
        self.add_comment(
            "Info",
            _("Платёж отменён пользователем {0}. Сумма: {1} USD, метод: {2}.").format(
                frappe.session.user,
                flt(self.amount, 2),
                self.payment_method or "-",
            ),
        )

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

        # Warn if UZS rows use different exchange rates (would silently book different rates to cashbox)
        uzs_rates = set()
        for row in self.payment_lines:
            if (row.currency or "USD").strip().upper() == "UZS" and flt(row.amount) > 0:
                rate = flt(row.exchange_rate or default_rate)
                if rate > 0:
                    uzs_rates.add(round(rate, 0))
        if len(uzs_rates) > 1:
            frappe.msgprint(
                _("Строки UZS имеют разные курсы: {0}. Убедитесь, что это корректно.").format(
                    ", ".join(str(int(r)) for r in sorted(uzs_rates))
                ),
                indicator="orange",
                alert=True,
            )

        self.amount = total
        if len(methods) > 1:
            self.payment_method = "Комбинированный"
        elif len(methods) == 1:
            self.payment_method = list(methods)[0]

def _send_payment_receipt_sms(doc):
    if not doc.customer:
        return
    cust = frappe.get_doc("Customer Profile", doc.customer, ignore_permissions=True)
    phone = cust.get_primary_phone()
    if not phone:
        return

    from nasiya365.utils.sms_manager import SMSManager

    amount_str = f"{flt(doc.amount):.2f} USD"
    ref = doc.reference_name or doc.name
    message = _("Nasiya365: Оплата {0} принята. Договор {1}. Спасибо!").format(amount_str, ref)
    SMSManager().send_sms(phone, message)


@frappe.whitelist()
def get_customer_installment_plans(customer):
    """Return all installment plans for a customer with debt + device info.

    Branch-scoped: non-admin users only see plans whose Sales Order branch is
    in their assigned branch list.
    """
    from nasiya365.permissions import require_branch_access
    from nasiya365.api.bnpl_dashboard import _user_branch_clause

    if not customer:
        return []
    # Permission check: caller must be able to read this customer (branch-scoped).
    require_branch_access("Customer Profile", customer, ptype="read")

    branch_clause, branch_params = _user_branch_clause("ip")
    rows = frappe.db.sql(
        f"""
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
            ON sei.name = ip.stock_entry
            OR (sei.parent = ip.stock_entry AND sei.idx = 1)
        LEFT JOIN `tabProduct` p
            ON p.name = sei.product
        LEFT JOIN `tabSales Order Item` soi
            ON soi.parent = ip.sales_order AND soi.idx = 1
        WHERE ip.customer = %s
          {branch_clause}
        ORDER BY ip.modified DESC
        """,
        (customer, *branch_params),
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
        ignore_permissions=True,
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
