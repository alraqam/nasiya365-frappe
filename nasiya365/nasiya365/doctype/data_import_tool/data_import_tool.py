# Copyright (c) 2024, Nasiya365 and contributors
# For license information, please see license.txt

import json
import os
import subprocess
import frappe
from frappe.model.document import Document
from nasiya365.data_import import import_bnpl_data

class DataImportTool(Document):
	pass


def run_import_standalone(doc_name, file_path, default_branch, import_type, skip_validation=False):
	"""
	Called via 'bench execute' in a separate process so db commits persist.
	Usage: bench --site SITE execute nasiya365.nasiya365.doctype.data_import_tool.data_import_tool.run_import_standalone --args '["doc_name", "/path/to/file.csv", "Branch", "Импорт договоров", false]'
	"""
	if not file_path or not os.path.exists(file_path):
		results = f"Ошибка: файл не найден: {file_path}"
	else:
		try:
			results = import_bnpl_data(file_path, default_branch, import_type, skip_validation=skip_validation)
		except Exception as e:
			import traceback
			error_trace = traceback.format_exc()
			frappe.log_error(error_trace, "Data Import Error")
			results = f"Ошибка импорта: {str(e)}\n\nПолная ошибка:\n{error_trace}"
	doc = frappe.get_doc("Data Import Tool", doc_name)
	doc.import_log = results or ""
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	return results



@frappe.whitelist()
def run_bnpl_import(doc_name):
	doc = frappe.get_doc("Data Import Tool", doc_name)

	if not doc.csv_file:
		frappe.throw("Прикрепите CSV файл.")

	if not doc.default_branch:
		frappe.throw("Выберите филиал по умолчанию.")

	file_doc = frappe.get_doc("File", {"file_url": doc.csv_file})
	file_path = file_doc.get_full_path()

	if not file_path or not os.path.exists(file_path):
		frappe.throw("Файл не найден на сервере. Загрузите файл заново и нажмите «Запустить импорт».")

	# Run import directly in-process (Docker-friendly, no subprocess issues)
	try:
		results = import_bnpl_data(file_path, doc.default_branch, doc.import_type, skip_validation=bool(doc.skip_validation))
	except Exception as e:
		import traceback
		error_trace = traceback.format_exc()
		frappe.log_error(error_trace, "Data Import Error")
		return f"Ошибка импорта: {str(e)}\n\nПолная ошибка:\n{error_trace}"
	
	doc.import_log = results
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	return results
