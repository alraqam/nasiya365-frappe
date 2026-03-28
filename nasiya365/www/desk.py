# Colocated with nasiya365/www/desk.html — delegate context to Frappe core desk.
no_cache = 1

from frappe.www.desk import get_context as frappe_desk_get_context


def get_context(context):
	return frappe_desk_get_context(context)
