import frappe
from frappe.model.document import Document
import re

class CustomerPhoneNumber(Document):
	def validate(self):
		self.validate_phone_format()
	
	def validate_phone_format(self):
		"""Validate Uzbek phone format: +998XXXXXXXXX or 9 digits"""
		if not self.phone_number:
			return
		
		# Remove spaces and dashes
		clean_phone = re.sub(r'[\s\-]', '', self.phone_number)
		
		# Check if it's 9 digits (short format)
		if re.match(r'^[0-9]{9}$', clean_phone):
			# Valid 9-digit format
			return
		
		# Check if it's full format: +998XXXXXXXXX
		if re.match(r'^\+998[0-9]{9}$', clean_phone):
			# Valid full format
			return
		
		frappe.throw(f"Invalid phone format. Use +998XXXXXXXXX or 9 digits (e.g., 901234567)")
