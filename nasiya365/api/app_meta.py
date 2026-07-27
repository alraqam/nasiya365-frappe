import frappe
from frappe.utils.jinja_globals import bundled_asset


@frappe.whitelist()
def asset_version():
	"""Current hashed URL of the app JS bundle — powers the client version nudge.

	Resolved through Frappe's own bundle resolver (assets.json), so the value tracks
	the latest `bench build`: a new deploy → a new hash. API responses aren't cached,
	so a long-lived desk tab polling this always sees the current build and can offer
	a reload. Requires an authenticated session (whitelist defaults to allow_guest=False),
	which the desk always has.
	"""
	return bundled_asset("nasiya365.bundle.js")
