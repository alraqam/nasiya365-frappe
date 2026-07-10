# -*- coding: utf-8 -*-
# Copyright (c) 2024, Nasiya365 and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import flt

class Supplier(Document):
	pass


@frappe.whitelist()
def get_supplier_balance(supplier, branch=None):
	"""Outstanding balance owed to this supplier, scoped to branch(es) the caller
	can see. If `branch` is given, restricted users must have access to it. If
	omitted, restricted users see the total across their own branches only;
	unrestricted users see the company-wide total."""
	from nasiya365.permissions import _get_user_branches, _is_unrestricted

	empty = {"total_purchased": 0, "total_paid": 0, "balance_due": 0}
	if not supplier:
		return empty

	user = frappe.session.user
	unrestricted = _is_unrestricted(user)

	if branch:
		if not unrestricted and branch not in _get_user_branches(user):
			frappe.throw(frappe._("Нет доступа к этому филиалу"), frappe.PermissionError)
		branches = [branch]
	elif unrestricted:
		branches = None  # no filter -> company-wide
	else:
		branches = _get_user_branches(user)
		if not branches:
			return empty

	branch_clause = ""
	params = [supplier]
	if branches is not None:
		placeholders = ",".join(["%s"] * len(branches))
		branch_clause = f" AND w.branch IN ({placeholders})"
		params.extend(branches)

	rows = frappe.db.sql(
		f"""
		SELECT
			COALESCE(SUM(se.total_value), 0) AS total_purchased,
			COALESCE(SUM(se.paid_amount), 0) AS total_paid,
			COALESCE(SUM(se.balance_due), 0) AS balance_due
		FROM `tabStock Entry` se
		INNER JOIN `tabWarehouse` w ON w.name = se.warehouse
		WHERE se.docstatus = 1
		  AND se.entry_type = 'Поступление'
		  AND se.supplier = %s
		  {branch_clause}
		""",
		tuple(params),
		as_dict=True,
	)
	r = rows[0] if rows else {}
	return {
		"total_purchased": flt(r.get("total_purchased")),
		"total_paid": flt(r.get("total_paid")),
		"balance_due": flt(r.get("balance_due")),
	}
