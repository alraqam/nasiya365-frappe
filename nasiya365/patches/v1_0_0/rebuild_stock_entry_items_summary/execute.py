import frappe


def execute():
	"""Backfill items_summary so Link fields show product name + attributes, not only STE id."""
	for name in frappe.get_all("Stock Entry", pluck="name"):
		doc = frappe.get_doc("Stock Entry", name)
		doc.set_items_summary()
		frappe.db.set_value(
			"Stock Entry",
			name,
			"items_summary",
			doc.items_summary,
			update_modified=False,
		)
	frappe.db.commit()
