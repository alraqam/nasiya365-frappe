# Copyright (c) 2024, Nasiya365 and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from nasiya365.data_import import import_bnpl_data

class DataImportTool(Document):
	pass

@frappe.whitelist()
def run_bnpl_import(doc_name):
	doc = frappe.get_doc("Data Import Tool", doc_name)
	
	if not doc.csv_file:
		frappe.throw("Please attach a CSV file first.")
		
	if not doc.default_branch:
		frappe.throw("Please select a Default Branch.")

	# Get file path
	file_doc = frappe.get_doc("File", {"file_url": doc.csv_file})
	file_path = file_doc.get_full_path()
	
	# Run import
	results = import_bnpl_data(file_path, doc.default_branch, doc.import_type, skip_validation=doc.skip_validation)
	
	return results
