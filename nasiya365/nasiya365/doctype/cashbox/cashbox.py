import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

# Payment methods that are USD cash in the physical drawer
_USD_CASH_METHODS = frozenset(["Наличные", "Наличные USD", "Акксессуар касса"])
# UZS cash — amount is stored in raw UZS (not converted)
_UZS_CASH_METHODS = frozenset(["Наличные UZS"])
# Card / P2P / digital transfers (USD-denominated)
_CARD_P2P_METHODS = frozenset(["Карта", "Click", "Payme", "Перевод"])
# POS terminal (USD-denominated)
_TERMINAL_METHODS = frozenset(["Терминал"])


def _row_to_usd(row, default_rate):
    """Convert a cashbox transaction row's amount to USD equivalent."""
    amount = flt(row.amount)
    currency = (row.currency or "USD").strip().upper()
    if currency == "USD":
        return amount
    rate = flt(getattr(row, "exchange_rate", 0) or default_rate)
    if rate > 0:
        return amount / rate
    # No rate available — log and return 0 to avoid inflating balance
    frappe.log_error(
        f"Cashbox transaction {getattr(row, 'name', '?')}: UZS amount {amount} has no exchange rate",
        "Cashbox: missing exchange rate",
    )
    return 0.0


class Cashbox(Document):
	def validate(self):
		self.calculate_totals()

	def calculate_totals(self):
		"""Calculate USD-equivalent totals and per-method breakdowns from transactions."""
		default_rate = flt(self.default_exchange_rate) or 12200.0
		income_rows = [t for t in (self.transactions or []) if t.transaction_type == "Приход"]
		expense_rows = [t for t in (self.transactions or []) if t.transaction_type == "Расход"]

		# USD-equivalent totals (UZS rows converted using their exchange_rate or default)
		self.total_income = round(sum(_row_to_usd(t, default_rate) for t in income_rows), 2)
		self.total_expense = round(sum(_row_to_usd(t, default_rate) for t in expense_rows), 2)
		self.closing_balance = round(
			flt(self.opening_balance) + self.total_income - self.total_expense, 2
		)

		# Per-method breakdown (raw amounts in their original currency)
		self.total_cash_usd = round(sum(
			flt(t.amount) for t in income_rows
			if (t.payment_method or "") in _USD_CASH_METHODS
		), 2)
		self.total_cash_uzs = round(sum(
			flt(t.amount) for t in income_rows
			if (t.payment_method or "") in _UZS_CASH_METHODS
		), 0)
		self.total_card_p2p = round(sum(
			flt(t.amount) for t in income_rows
			if (t.payment_method or "") in _CARD_P2P_METHODS
		), 2)
		self.total_terminal = round(sum(
			flt(t.amount) for t in income_rows
			if (t.payment_method or "") in _TERMINAL_METHODS
		), 2)

	def close_cashbox(self):
		"""Close the cashbox (no transfer)."""
		self.status = "Закрыта"
		self.closing_date = today()
		self.save()


def _find_master_cashbox(branch=None):
	"""Return the open master cashbox for the branch (or any open master if no branch)."""
	filters = {"is_master": 1, "status": "Открыта"}
	if branch:
		filters["branch"] = branch
	return frappe.db.get_value(
		"Cashbox", filters, "name",
		order_by="opening_date desc, modified desc",
	)


@frappe.whitelist()
def get_master_cashbox(branch=None):
	"""Return the open master cashbox name for the given branch."""
	return _find_master_cashbox(branch)


@frappe.whitelist()
def close_and_transfer(cashbox_name, to_cashbox=None):
	"""
	Close a salesperson cashbox and create a Cash Handover pending manager confirmation.
	Returns the new Cash Handover name.
	"""
	doc = frappe.get_doc("Cashbox", cashbox_name)

	if doc.status == "Закрыта":
		frappe.throw(_("Касса уже закрыта"))

	if not to_cashbox:
		to_cashbox = _find_master_cashbox(doc.branch)
	if not to_cashbox:
		frappe.throw(_("Не найдена открытая главная касса для данного филиала. Укажите её вручную."))
	if to_cashbox == cashbox_name:
		frappe.throw(_("Нельзя передать кассу самой себе"))

	# Compute per-method income totals (keyed by method + currency)
	lines = {}
	rates = {}  # store exchange_rate per (method, currency) key
	for t in (doc.transactions or []):
		if t.transaction_type != "Приход":
			continue
		key = (t.payment_method or "Другое", (t.currency or "USD").upper())
		lines[key] = lines.get(key, 0.0) + flt(t.amount)
		if flt(getattr(t, "exchange_rate", 0)) > 0:
			rates[key] = flt(t.exchange_rate)

	if not any(v > 0 for v in lines.values()):
		frappe.throw(_("Нет приходных транзакций для передачи"))

	# Wrap handover-create + source-cashbox-debit in one savepoint so a failure
	# on the second step does not leave an orphan handover with no matching debit.
	frappe.db.savepoint("nasiya_close_and_transfer")
	try:
		handover = frappe.new_doc("Cash Handover")
		handover.from_cashbox = cashbox_name
		handover.to_cashbox = to_cashbox
		handover.branch = doc.branch
		handover.transferred_by = frappe.session.user
		handover.transfer_date = today()
		for (method, currency), amount in sorted(lines.items()):
			if amount > 0:
				handover.append("lines", {
					"payment_method": method,
					"currency": currency,
					"amount": amount,
				})
		handover.insert(ignore_permissions=True)

		# Debit salesperson cashbox — one Расход row per method
		for (method, currency), amount in sorted(lines.items()):
			if amount > 0:
				doc.append("transactions", {
					"transaction_type": "Расход",
					"payment_method": method,
					"currency": currency,
					"exchange_rate": rates.get((method, currency), 0),
					"amount": amount,
					"category": "Перевод",
					"reference_doctype": "Cash Handover",
					"reference_name": handover.name,
					"notes": f"Инкассация → {to_cashbox} [HAND:{handover.name}]",
				})

		doc.status = "Закрыта"
		doc.closing_date = today()
		doc.save(ignore_permissions=True)
	except Exception:
		frappe.db.rollback(save_point="nasiya_close_and_transfer")
		raise

	return handover.name


@frappe.whitelist()
def add_transaction(cashbox, transaction_type, amount, category, notes="",
                    payment_method=None, currency="USD", exchange_rate=0,
                    reference_doctype=None, reference_name=None):
	"""Add a transaction to an open cashbox."""
	doc = frappe.get_doc("Cashbox", cashbox)

	if doc.status == "Закрыта":
		frappe.throw(_("Нельзя добавить транзакцию в закрытую кассу"))

	doc.append("transactions", {
		"transaction_type": transaction_type,
		"payment_method": payment_method,
		"currency": currency or "USD",
		"exchange_rate": flt(exchange_rate),
		"amount": amount,
		"category": category,
		"notes": notes,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
	})

	doc.save()
	return doc
