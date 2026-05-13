"""
Trade In DocType — record a customer-brought device, intake to stock, optional payout.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, today


class TradeIn(Document):
    """Trade In state machine:

        docstatus=0, status=Черновик  → editable
        docstatus=1, status=Принят    → Stock Entry created; side-effects committed
        docstatus=2, status=Отменен   → all side-effects reversed
    """

    def before_insert(self):
        if not self.created_by:
            self.created_by = frappe.session.user
        if not self.trade_in_date:
            self.trade_in_date = today()

    def validate(self):
        if self.docstatus == 0 and not (self.status or "").strip():
            self.status = "Черновик"
        self._validate_branch_access()
        self._validate_payout_target()

    def _validate_branch_access(self):
        from nasiya365.permissions import _get_user_branches, _is_unrestricted

        user = frappe.session.user
        if _is_unrestricted(user):
            return
        if self.branch and self.branch not in _get_user_branches(user):
            frappe.throw(
                _("Нет доступа к филиалу {0}").format(self.branch),
                frappe.PermissionError,
            )

    def _validate_payout_target(self):
        method = (self.payout_method or "").strip()
        if method == "В счёт покупки":
            if not self.linked_sales_order:
                frappe.throw(_("Для зачёта обмена укажите Sales Order"))
            so = frappe.db.get_value(
                "Sales Order",
                self.linked_sales_order,
                ["docstatus", "customer"],
                as_dict=True,
            )
            if not so:
                frappe.throw(_("Sales Order {0} не найден").format(self.linked_sales_order))
            if cint(so.docstatus) >= 1:
                frappe.throw(
                    _("Sales Order {0} уже проведён — зачёт обмена недоступен.").format(
                        self.linked_sales_order
                    )
                )
            if so.customer != self.customer:
                frappe.throw(
                    _("Клиент в обмене ({0}) и заказе ({1}) должен совпадать").format(
                        self.customer, so.customer
                    )
                )
        elif method == "Наличные":
            if not self.payout_currency:
                self.payout_currency = "USD"

    # ---------- lifecycle hooks ----------

    def on_submit(self):
        self._create_stock_entry()
        method = (self.payout_method or "").strip()
        if method == "Наличные":
            self._record_cashbox_payout()
        elif method == "В счёт покупки":
            self._apply_credit_to_sales_order()
        self.db_set("status", "Принят", update_modified=False)

    def before_cancel(self):
        method = (self.payout_method or "").strip()
        # Reverse side-effects BEFORE Frappe writes docstatus=2 so a failure aborts cleanly.
        if method == "В счёт покупки" and self.linked_sales_order:
            self._reverse_credit_on_sales_order()
        if method == "Наличные" and self.cashbox_transaction_marker:
            self._reverse_cashbox_payout()
        if self.stock_entry:
            se_docstatus = frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus")
            if cint(se_docstatus) == 1:
                se = frappe.get_doc("Stock Entry", self.stock_entry)
                se.flags.ignore_permissions = True
                # We're inside Trade In.before_cancel; the Trade In itself is still
                # docstatus=1 and still links to this SE, so Frappe's check_no_back_links
                # would refuse the SE cancel. Tell it to skip the check — the parent's
                # docstatus flips to 2 right after this method returns.
                se.flags.ignore_links = True
                se.cancel()

    def on_cancel(self):
        self.db_set("status", "Отменен", update_modified=False)
        self.add_comment(
            "Info",
            _("Обмен отменён пользователем {0}. Сумма оценки: {1}.").format(
                frappe.session.user, flt(self.appraisal_amount, 2)
            ),
        )

    def on_trash(self):
        from nasiya365.permissions import admin_only_for_submitted_delete

        admin_only_for_submitted_delete(self)
        # Drafts with no side-effects can be deleted freely. If somehow a stock_entry
        # exists for a draft Trade In (shouldn't happen normally), block.
        if self.stock_entry and frappe.db.exists("Stock Entry", self.stock_entry):
            frappe.throw(
                _(
                    "Невозможно удалить обмен: связано поступление {0}. Сначала отмените обмен."
                ).format(self.stock_entry)
            )

    # ---------- side-effect implementations ----------

    def _create_stock_entry(self):
        """Create a submitted Поступление Stock Entry for the traded-in device."""
        se = frappe.new_doc("Stock Entry")
        se.entry_type = "Поступление"
        se.warehouse = self.warehouse
        se.posting_date = self.trade_in_date
        # Stock Entry Item's `condition` Select only accepts "Новый" / "Б/У".
        # Trade-in devices are always used (the granular appraisal grade lives on the
        # Trade In doc itself).
        se.append(
            "items",
            {
                "product": self.product,
                "imei": (self.imei or "").strip(),
                "color": self.color,
                "storage": self.storage,
                "condition": "Б/У",
                "battery_cycles": self.battery_cycles,
                "quantity": 1,
                "rate": flt(self.appraisal_amount),
            },
        )
        se.flags.ignore_permissions = True
        se.insert()
        se.submit()
        # Store the reference back on this Trade In via db_set (we're inside on_submit
        # and Frappe is about to db_update — both writes coexist fine).
        self.db_set("stock_entry", se.name, update_modified=False)

    def _record_cashbox_payout(self):
        """Add a 'Расход / Выкуп товара' row to the branch master cashbox."""
        from nasiya365.nasiya365.doctype.cashbox.cashbox import _find_master_cashbox

        cashbox_name = _find_master_cashbox(self.branch)
        if not cashbox_name:
            frappe.throw(
                _(
                    "Не найдена открытая главная касса для филиала {0}. Откройте кассу и повторите."
                ).format(self.branch)
            )

        marker = f"[TI:{self.name}]"
        payment_method = "Наличные USD" if (self.payout_currency or "USD").upper() == "USD" else "Наличные UZS"
        cashbox = frappe.get_doc("Cashbox", cashbox_name)
        cashbox.append(
            "transactions",
            {
                "transaction_type": "Расход",
                "payment_method": payment_method,
                "currency": (self.payout_currency or "USD").upper(),
                "amount": flt(self.appraisal_amount),
                "category": "Выкуп товара",
                "reference_doctype": "Trade In",
                "reference_name": self.name,
                "notes": f"Выкуп {self.imei or '?'} у {self.customer_name or self.customer} {marker}",
            },
        )
        cashbox.flags.ignore_permissions = True
        cashbox.save()
        self.db_set("cashbox_transaction_marker", marker, update_modified=False)

    def _apply_credit_to_sales_order(self):
        """Bump paid_amount on the linked draft Sales Order by the appraisal value
        and set the SO.trade_in back-reference."""
        so = frappe.get_doc("Sales Order", self.linked_sales_order)
        if cint(so.docstatus) >= 1:
            frappe.throw(
                _("Sales Order {0} уже проведён — зачёт обмена недоступен.").format(so.name)
            )
        if so.trade_in and so.trade_in != self.name:
            frappe.throw(
                _("К заказу {0} уже привязан другой обмен ({1}).").format(so.name, so.trade_in)
            )
        so.paid_amount = flt(so.paid_amount) + flt(self.appraisal_amount)
        so.trade_in = self.name
        so.flags.ignore_permissions = True
        so.save()

    def _reverse_credit_on_sales_order(self):
        """Subtract appraisal_amount from the linked SO's paid_amount and clear
        the trade_in back-reference. If the SO is already submitted, paid_amount
        is frozen — log and continue (audit trail remains)."""
        so = frappe.get_doc("Sales Order", self.linked_sales_order)
        if cint(so.docstatus) >= 1:
            frappe.log_error(
                f"Trade In {self.name}: cannot reverse credit on submitted SO {so.name}",
                "Trade In: SO frozen on cancel",
            )
            return
        so.paid_amount = max(0.0, flt(so.paid_amount) - flt(self.appraisal_amount))
        if so.trade_in == self.name:
            so.trade_in = None
        so.flags.ignore_permissions = True
        so.save()

    def _reverse_cashbox_payout(self):
        """Remove the cashbox row created by _record_cashbox_payout."""
        cashboxes = frappe.get_all(
            "Cashbox",
            filters={"status": ["in", ["Открыта", "Закрыта"]]},
            pluck="name",
        )
        for cashbox_name in cashboxes:
            cashbox = frappe.get_doc("Cashbox", cashbox_name)
            before = len(cashbox.transactions or [])
            cashbox.transactions = [
                t
                for t in (cashbox.transactions or [])
                if not (
                    (t.reference_doctype or "") == "Trade In"
                    and (t.reference_name or "") == self.name
                )
            ]
            if len(cashbox.transactions or []) != before:
                cashbox.flags.ignore_permissions = True
                cashbox.save()
