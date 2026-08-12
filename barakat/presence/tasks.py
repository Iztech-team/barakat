"""Scheduled work.

Reports only ever add. Nothing in a watcher's message says "this person left" — a
departure is the *absence* of a sighting, which no incoming request can announce. So
without the sweep below, a shop fills up with people who arrived and never left.
"""

import frappe
from frappe.utils import add_days, now_datetime

from barakat.presence import service
from barakat.presence.mode import WIFI, settings_for


def enabled_companies():
	"""Only companies that deliberately switched wifi presence on.

	Never "all companies" and never "all sites". `petromall` shares this bench, has the
	`barakat` app installed, and is not ours — a loop over everything would sweep it in.
	The switch being off is what keeps it, and every untouched company, untouched.
	"""
	return frappe.get_all(
		"Presence Settings",
		filters={"mode": WIFI},
		pluck="custom_company",
	)


def sweep_departures():
	"""Age out anyone no till has seen for the wait. Runs every minute."""
	for company in enabled_companies():
		branches = frappe.get_all(
			"Presence Till",
			filters={"custom_company": company, "status": "Active"},
			pluck="branch",
			distinct=True,
		)
		for branch in branches:
			try:
				service.sweep(branch, company)
			except Exception:
				# One bad branch must not stop the others. A shop whose sweep throws
				# would otherwise take every other shop's departures down with it.
				frappe.log_error(
					title=f"presence sweep failed: {branch}",
					message=frappe.get_traceback(),
				)
		frappe.db.commit()


def delete_old_sightings():
	"""Drop raw sightings past their retention. Runs daily.

	Sightings are a minute-by-minute record of where named people were. Keeping the
	detail forever is a liability with no upside; the daily summary lives on in
	`Presence Session`.
	"""
	for company in enabled_companies():
		days = settings_for(company)["sighting_retention_days"]
		cutoff = add_days(now_datetime(), -days)
		frappe.db.delete(
			"Presence Sighting",
			{"custom_company": company, "server_time": ("<", cutoff)},
		)
		frappe.db.commit()
