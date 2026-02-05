import frappe
from frappe.model.document import Document
from frappe.utils import flt, today

class Cashbox(Document):
	def validate(self):
		self.calculate_totals()
	
	def calculate_totals(self):
		"""Calculate total income and expense from transactions"""
		self.total_income = sum([flt(t.amount) for t in self.transactions if t.transaction_type == "Income"])
		self.total_expense = sum([flt(t.amount) for t in self.transactions if t.transaction_type == "Expense"])
		self.closing_balance = flt(self.opening_balance) + flt(self.total_income) - flt(self.total_expense)
	
	def close_cashbox(self):
		"""Close the cashbox"""
		self.status = "Closed"
		self.closing_date = today()
		self.save()


@frappe.whitelist()
def add_transaction(cashbox, transaction_type, amount, category, notes="", reference_doctype=None, reference_name=None):
	"""Add a transaction to an open cashbox"""
	doc = frappe.get_doc("Cashbox", cashbox)
	
	if doc.status == "Closed":
		frappe.throw("Cannot add transactions to a closed cashbox")
	
	doc.append("transactions", {
		"transaction_type": transaction_type,
		"amount": amount,
		"category": category,
		"notes": notes,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name
	})
	
	doc.save()
	return doc
