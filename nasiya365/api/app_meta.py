import frappe
from frappe.utils.jinja_globals import bundled_asset


@frappe.whitelist()
def asset_version():
	"""Current hashed URLs of the app JS + CSS bundles — powers the client version nudge.

	Resolved through Frappe's own bundle resolver (assets.json), so each value tracks
	the latest `bench build`: a new deploy → a new hash. API responses aren't cached,
	so a long-lived desk tab polling this always sees the current build and can offer
	a reload. Both bundles are reported so a CSS-only deploy still triggers the nudge.
	Requires an authenticated session (whitelist defaults to allow_guest=False), which
	the desk always has. Never raises: on any resolver error it returns empty strings so
	the client simply no-ops instead of the endpoint 500-ing on every poll.
	"""
	try:
		return {
			"js": bundled_asset("nasiya365.bundle.js"),
			"css": bundled_asset("nasiya365.bundle.css"),
		}
	except Exception:
		return {"js": "", "css": ""}
