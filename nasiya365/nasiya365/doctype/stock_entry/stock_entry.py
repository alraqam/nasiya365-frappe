import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

class StockEntry(Document):
	def validate(self):
		self.calculate_totals()
		self.set_items_summary()
		self.set_business_status()

	def _has_business_status_field(self):
		return bool(self.meta.get_field("business_status"))

	def set_items_summary(self):
		"""Human-readable line summary for list views and Link field titles (name + attributes)."""
		summary_items = []
		for item in self.items[:3]:
			if not item.product:
				continue

			product_name = frappe.get_cached_value("Product", item.product, "product_name")
			brand = frappe.get_cached_value("Product", item.product, "brand")

			parts = []
			base = (product_name or item.product or "").strip()
			if base:
				parts.append(base)

			color = (getattr(item, "color", None) or "").strip()
			if color:
				parts.append(color)
			storage = (getattr(item, "storage", None) or "").strip()
			if storage:
				parts.append(storage)
			capacity = (getattr(item, "capacity", None) or "").strip()
			if capacity:
				parts.append(capacity)

			item_desc = " · ".join(parts) if parts else (item.product or "")

			if item.serial_no:
				serial = (item.serial_no or "").strip()
				if len(serial) > 6:
					serial = "…" + serial[-6:]
				item_desc = f"{item_desc} ({serial})"

			if brand:
				item_desc = f"{item_desc} — {brand}"

			summary_items.append(item_desc)

		if len(self.items) > 3:
			summary_items.append(f"… (+{len(self.items) - 3})")

		self.items_summary = ", ".join(summary_items)

	
	def calculate_totals(self):
		"""Calculate total quantity and value"""
		self.total_quantity = sum([flt(item.quantity) for item in self.items])
		self.total_value = sum([flt(item.amount) for item in self.items])
		
		# Calculate amount for each item
		for item in self.items:
			item.amount = flt(item.quantity) * flt(item.rate)
	
	def on_submit(self):
		"""Update stock ledger when submitted"""
		if self._has_business_status_field():
			self.business_status = self._resolve_sale_source_status()
			self.db_update()
		self.update_stock_ledger()
	
	def on_cancel(self):
		"""Reverse stock ledger entries when cancelled"""
		if self._has_business_status_field():
			self.business_status = "Возврат"
			self.db_update()
		self.update_stock_ledger(cancel=True)

	def set_business_status(self):
		if not self._has_business_status_field():
			return
		# Keep operator-selected value; only apply defaults when field is empty.
		if self.docstatus == 0 and not (getattr(self, "business_status", None) or "").strip():
			self.business_status = "Черновик"
		elif self.docstatus == 1 and not (getattr(self, "business_status", None) or "").strip():
			self.business_status = "Проведен"

	def _resolve_sale_source_status(self):
		manual = (getattr(self, "business_status", None) or "").strip()
		if manual in ("Naqd savdo", "Rassrochka savdo", "Проведен"):
			return manual
		remarks_lower = (self.remarks or "").lower()
		if "рассрочка" in remarks_lower or "rassrochka" in remarks_lower:
			return "Rassrochka savdo"
		if "наличные" in remarks_lower or "naqd" in remarks_lower:
			return "Naqd savdo"
		return "Проведен"
	
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
