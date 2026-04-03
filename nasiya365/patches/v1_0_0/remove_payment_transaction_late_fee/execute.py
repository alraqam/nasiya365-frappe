import frappe


def execute():
	"""Remove stale «Пеня» (late_fee_applied) from DB/UI if JSON already dropped the field."""
	dt = "Payment Transaction"
	fn = "late_fee_applied"
	table = f"tab{dt}"

	frappe.db.sql("DELETE FROM `tabDocField` WHERE parent=%s AND fieldname=%s", (dt, fn))
	frappe.db.sql("DELETE FROM `tabCustom Field` WHERE dt=%s AND fieldname=%s", (dt, fn))
	frappe.db.sql(
		"DELETE FROM `tabProperty Setter` WHERE doc_type=%s AND field_name=%s",
		(dt, fn),
	)
	frappe.db.commit()

	if fn in frappe.db.get_table_columns(dt):
		frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{fn}`")

	frappe.db.commit()
	frappe.clear_cache(doctype=dt)
