import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

class StockEntry(Document):
	def validate(self):
		self.calculate_totals()
	
	def calculate_totals(self):
		"""Calculate total quantity and value"""
		self.total_quantity = sum([flt(item.quantity) for item in self.items])
		self.total_value = sum([flt(item.amount) for item in self.items])
		
		# Calculate amount for each item
		for item in self.items:
			item.amount = flt(item.quantity) * flt(item.rate)
	
	def on_submit(self):
		"""Update stock ledger when submitted"""
		self.update_stock_ledger()
	
	def on_cancel(self):
		"""Reverse stock ledger entries when cancelled"""
		self.update_stock_ledger(cancel=True)
	
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
