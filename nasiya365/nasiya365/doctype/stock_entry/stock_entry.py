import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

def refresh_stock_entry_business_status(stock_entry_name, exclude_plan_name=None):
	"""Recompute and save business_status based on how many items are sold (by serial/IMEI).

	`exclude_plan_name` — skip this plan when counting (used during on_trash before the row is deleted).
	"""
	if not stock_entry_name:
		return
	ste_meta = frappe.db.get_value(
		"Stock Entry", stock_entry_name, ["docstatus", "entry_type"], as_dict=True
	)
	if not ste_meta or ste_meta.docstatus != 1:
		return  # Only refresh submitted entries

	items = frappe.get_all(
		"Stock Entry Item",
		filters={"parent": stock_entry_name},
		fields=["imei"],
		order_by="idx asc",
	)
	total = len(items)
	serials = [(i.imei or "").strip() for i in items if (i.imei or "").strip()]

	if not serials:
		return  # No serials — cannot track per item

	exclude = (exclude_plan_name or "").strip()
	installment_sold = 0
	cash_sold = 0

	for serial in serials:
		norm = serial.upper().replace(" ", "")
		# Exact IMEI match only. A last-6-digit fallback here miscounts a unit as sold when
		# a DIFFERENT phone shares its last six digits — the multi-IMEI-receipt landmine.

		if frappe.db.sql(
			"""SELECT 1 FROM `tabInstallment Plan`
			   WHERE IFNULL(docstatus, 0) < 2
			     AND (%s = '' OR name != %s)
			     AND NULLIF(TRIM(imei), '') IS NOT NULL
			     AND REPLACE(UPPER(TRIM(imei)),' ','') = %s
			   LIMIT 1""",
			(exclude, exclude, norm),
		):
			installment_sold += 1
			continue

		if frappe.db.sql(
			"""SELECT 1 FROM `tabSales Order Item` soi
			   INNER JOIN `tabSales Order` so ON so.name = soi.parent
			   WHERE so.docstatus = 1
			     AND NULLIF(TRIM(soi.imei), '') IS NOT NULL
			     AND REPLACE(UPPER(TRIM(soi.imei)),' ','') = %s
			   LIMIT 1""",
			(norm,),
		):
			cash_sold += 1

	sold = installment_sold + cash_sold

	# Simple availability model: a receipt with any unsold unit is "В наличии"; once every
	# unit is sold — by any method (cash, installment, or a mix) — it is "Продано". The sale
	# method is visible on the Sales Orders / Installment Plans themselves, so it isn't
	# duplicated into the receipt status.
	status = "Продано" if sold >= total else "В наличии"

	frappe.db.set_value(
		"Stock Entry", stock_entry_name, "business_status", status, update_modified=False
	)


class StockEntry(Document):
	def validate(self):
		self.calculate_totals()
		self.set_items_summary()
		self.set_business_status()
		self.set_payment_status()

	def _has_business_status_field(self):
		return bool(self.meta.get_field("business_status"))

	def set_items_summary(self):
		"""Human-readable summary grouped by product: 'iPhone 16 Pro (…1234, …5678) — Apple'."""
		from collections import OrderedDict

		groups: dict = OrderedDict()
		for item in self.items:
			if not item.product:
				continue
			groups.setdefault(item.product, []).append(item)

		MAX_GROUPS = 3
		parts = []

		for product, rows in list(groups.items())[:MAX_GROUPS]:
			product_name = (frappe.get_cached_value("Product", product, "product_name") or product).strip()
			brand = (frappe.get_cached_value("Product", product, "brand") or "").strip()

			colors = {(r.color or "").strip() for r in rows if (r.color or "").strip()}
			storages = {(r.storage or "").strip() for r in rows if (r.storage or "").strip()}
			capacities = {(getattr(r, "capacity", None) or "").strip() for r in rows if (getattr(r, "capacity", None) or "").strip()}

			attrs = [product_name]
			if len(colors) == 1:
				attrs.append(next(iter(colors)))
			if len(storages) == 1:
				attrs.append(next(iter(storages)))
			if len(capacities) == 1:
				attrs.append(next(iter(capacities)))

			base = " · ".join(attrs)

			n = len(rows)
			serials = []
			for r in rows:
				s = (r.imei or "").strip()
				if s:
					serials.append("…" + s[-6:] if len(s) > 6 else s)

			if n == 1:
				if serials:
					base = f"{base} ({serials[0]})"
			elif serials:
				shown = ", ".join(serials[:3])
				base = f"{base} ({shown}{'…' if n > 3 else ''})"
			else:
				base = f"{base} ×{n}"

			if brand:
				base = f"{base} — {brand}"

			parts.append(base)

		extra = len(groups) - MAX_GROUPS
		if extra > 0:
			parts.append(f"+{extra}")

		self.items_summary = ", ".join(parts)

	
	def calculate_totals(self):
		"""Calculate total quantity, value and expense"""
		for item in self.items:
			item.amount = flt(item.quantity) * flt(item.rate)

		self.total_quantity = sum(flt(item.quantity) for item in self.items)
		self.total_value = sum(flt(item.amount) for item in self.items)
		self.total_expense = sum(flt(item.expense) for item in self.items)
	
	def on_submit(self):
		"""Update stock ledger when submitted"""
		# Only apply the default when the operator never picked a real status —
		# "Черновик" is the draft-time default, not a deliberate choice, so it
		# still needs replacing; anything else (e.g. a manually corrected value)
		# is respected instead of being silently overwritten.
		if self._has_business_status_field() and (self.business_status or "").strip() in ("", "Черновик"):
			self.business_status = "В наличии"
			self.db_update()
		self.update_stock_ledger()

	def on_cancel(self):
		"""Reverse stock ledger entries when cancelled"""
		if self._has_business_status_field() and (self.business_status or "").strip() in ("", "Черновик"):
			self.business_status = "Возврат"
			self.db_update()
		self.update_stock_ledger(cancel=True)

	def on_trash(self):
		"""Block deletion if any Installment Plan references this Stock Entry — the
		plan's stock_entry FK would go stale and the BNPL flow would break."""
		from nasiya365.permissions import admin_only_for_submitted_delete

		admin_only_for_submitted_delete(self)
		# Check both legacy parent ref and per-line SEI ref.
		ip_count_parent = frappe.db.count(
			"Installment Plan", {"stock_entry": self.name}
		)
		ip_count_line = frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabInstallment Plan` ip
			   INNER JOIN `tabStock Entry Item` sei ON sei.name = ip.stock_entry
			   WHERE sei.parent = %s""",
			(self.name,),
		)[0][0]
		total = (ip_count_parent or 0) + (ip_count_line or 0)
		if total:
			frappe.throw(
				frappe._(
					"Невозможно удалить поступление: оно связано с {0} планом(и) рассрочки."
				).format(total)
			)

	def set_business_status(self):
		if not self._has_business_status_field():
			return
		# Keep operator-selected value; only apply defaults when field is empty.
		if self.docstatus == 0 and not (getattr(self, "business_status", None) or "").strip():
			self.business_status = "Черновик"
		elif self.docstatus == 1 and not (getattr(self, "business_status", None) or "").strip():
			self.business_status = "В наличии"
	
	def set_payment_status(self):
		"""Recompute balance_due/payment_status from total_value - paid_amount.

		paid_amount itself is only ever changed externally (by Supplier Payment
		allocation, via frappe.db.set_value) -- never reset here, only read, so a
		later edit that changes total_value (e.g. correcting an item's rate)
		still nets correctly against whatever has already been paid.
		"""
		if self.entry_type != "Поступление" or not self.meta.get_field("balance_due"):
			return
		self.balance_due = flt(self.total_value) - flt(self.paid_amount)
		if flt(self.paid_amount) <= 0:
			self.payment_status = "Не оплачено"
		elif self.balance_due <= 0.001:
			self.payment_status = "Оплачено"
		else:
			self.payment_status = "Частично оплачено"

	def update_stock_ledger(self, cancel=False):
		"""Create stock ledger entries"""
		for item in self.items:
			# Determine quantity based on entry type
			qty_change = flt(item.quantity)
			
			if self.entry_type == "Отпуск":  # Issue
				qty_change = -qty_change
			elif self.entry_type == "Корректировка":  # Adjustment
				# For adjustments, quantity can be positive or negative
				pass
			
			if cancel:
				qty_change = -qty_change
			
			# Create stock ledger entry
			ledger_entry = frappe.get_doc({
				"doctype": "Stock Ledger",
				"product": item.product,
				"warehouse": self.warehouse,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"reference_doctype": "Stock Entry",
				"reference_name": self.name,
				"quantity_change": qty_change,
				"balance_quantity": self.get_stock_balance(item.product, self.warehouse) + qty_change,
				"valuation_rate": item.rate,
				"stock_value": flt(item.quantity) * flt(item.rate),
				"stock_value_difference": qty_change * flt(item.rate)
			})
			ledger_entry.insert(ignore_permissions=True)
			
			# Handle transfer to another warehouse
			if self.entry_type == "Перемещение" and self.to_warehouse:
				to_ledger = frappe.get_doc({
					"doctype": "Stock Ledger",
					"product": item.product,
					"warehouse": self.to_warehouse,
					"posting_date": self.posting_date,
					"posting_time": self.posting_time,
					"reference_doctype": "Stock Entry",
					"reference_name": self.name,
					"quantity_change": -qty_change if cancel else qty_change,
					"balance_quantity": self.get_stock_balance(item.product, self.to_warehouse) + qty_change,
					"valuation_rate": item.rate,
					"stock_value": flt(item.quantity) * flt(item.rate),
					"stock_value_difference": qty_change * flt(item.rate)
				})
				to_ledger.insert(ignore_permissions=True)
	
	def get_stock_balance(self, product, warehouse):
		"""Get current stock balance"""
		result = frappe.db.sql("""
			SELECT COALESCE(SUM(quantity_change), 0) as balance
			FROM `tabStock Ledger`
			WHERE product = %s AND warehouse = %s
		""", (product, warehouse), as_dict=True)
		
		return flt(result[0].balance) if result else 0
