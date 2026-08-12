"""Pairing a phone to a person by scanning a QR code.

The problem this solves: a phone cannot tell a server which phone it is. Browsers do
not expose the device's network id, and every phone in a shop reaches the outside world
through the same router address, so from ERPNext they are indistinguishable.

Only something INSIDE the shop can tell them apart. This is the same principle a captive
portal works on - the difference is that a captive portal runs on the router, and we
cannot rely on the router, so the till plays that part instead.

The flow:

  1. A manager clicks Pair. We mint a short-lived one-use code and hand back a URL
     pointing at that branch's till, on the shop's own network.
  2. The screen shows it as a QR. The staff member scans it with their camera.
  3. Their phone opens the till's local page. The till sees which phone is asking,
     matches it against the devices it is already scanning, and calls `claim` below.
  4. The pairing is created. The manager's screen stops waiting and says done.

The code never leaves the shop except inside the QR on the manager's screen, is good for
two minutes, and works once.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from barakat.presence import keys
from barakat.presence.mode import is_wifi_mode, settings_for

SESSION = "Presence Pairing Session"


@frappe.whitelist()
def start(employee, branch):
	"""Open a pairing window for this person at this branch. Manager work."""
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_value(
		"Employee", employee, "company"
	)
	if not company:
		frappe.throw(_("Cannot tell which company this employee belongs to."))

	if not is_wifi_mode(company):
		frappe.throw(_("Wifi presence is not enabled for this company."))

	frappe.get_doc("Employee", employee).check_permission("write")

	till = _reporting_till(branch, company)
	if not till:
		frappe.throw(
			_("No till at {0} is reporting right now, so nothing can see the phone.").format(
				branch
			)
		)
	if not till.local_url:
		frappe.throw(
			_("Till {0} has not said where it can be reached on the shop network yet.").format(
				till.name
			)
		)

	# One open window per person at a time. A second click replaces the first rather
	# than leaving two codes alive.
	frappe.db.delete(SESSION, {"employee": employee, "state": "Waiting"})

	timeout = settings_for(company)["pairing_timeout_s"]
	code = frappe.generate_hash(length=12)

	frappe.get_doc(
		{
			"doctype": SESSION,
			"custom_company": company,
			"branch": branch,
			"employee": employee,
			"code": code,
			"state": "Waiting",
			"expires_at": add_to_date(now_datetime(), seconds=timeout),
		}
	).insert(ignore_permissions=True)

	return {
		"code": code,
		"url": f"{till.local_url.rstrip('/')}/pair?code={code}",
		"expires_in_s": timeout,
	}


@frappe.whitelist()
def status(code):
	"""Has the phone scanned yet? Polled by the manager's screen."""
	row = frappe.db.get_value(
		SESSION,
		{"code": code},
		["name", "state", "employee", "device_key", "expires_at", "custom_company"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Unknown pairing code."), frappe.DoesNotExistError)

	if row.state == "Waiting" and row.expires_at < now_datetime():
		frappe.db.set_value(SESSION, row.name, "state", "Expired")
		row.state = "Expired"

	return {
		"state": row.state,
		"employee": row.employee,
		"deviceKey": row.device_key,
		"shownAs": (row.device_key or "")[-4:],
	}


@frappe.whitelist()
def claim(code, device_key):
	"""Called BY THE TILL when a phone opens its local page.

	The till is the only party that can answer "which phone is this", so it is the only
	party allowed to call this. It authenticates with its own key, and the code decides
	which person the phone belongs to - so a till cannot pair somebody it was not asked
	to, and a stolen code is useless without a till at the right branch.
	"""
	till = keys.till_for_current_user()
	if not till:
		frappe.throw(_("Only a till may claim a pairing."), frappe.PermissionError)
	if till.status != "Active":
		frappe.throw(_("This till is {0}.").format(till.status), frappe.PermissionError)

	row = frappe.db.get_value(
		SESSION,
		{"code": code},
		["name", "state", "employee", "branch", "custom_company", "expires_at"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Unknown pairing code."), frappe.DoesNotExistError)

	if row.custom_company != till.custom_company or row.branch != till.branch:
		# The code belongs to a different branch. A till may only answer for its own.
		frappe.throw(_("That code is not for this branch."), frappe.PermissionError)

	if row.state != "Waiting":
		frappe.throw(_("That code has already been used."))
	if row.expires_at < now_datetime():
		frappe.db.set_value(SESSION, row.name, "state", "Expired")
		frappe.throw(_("That code has expired."))

	device_key = str(device_key).strip().lower()[:64]
	if not device_key:
		frappe.throw(_("No device was identified."))

	_pair(row.employee, device_key, row.custom_company)

	frappe.db.set_value(
		SESSION,
		row.name,
		{
			"state": "Claimed",
			"device_key": device_key,
			"till": till.name,
			"claimed_at": now_datetime(),
		},
	)
	return {"ok": True, "employee": row.employee}


def _pair(employee, device_key, company):
	"""Attach the device, closing whoever held it before.

	Closed with a date, never deleted - delete January's pairing and January's
	attendance stops being explicable.
	"""
	today = now_datetime().date()

	for name in frappe.get_all(
		"Employee Device",
		filters={
			"device_key": device_key,
			"custom_company": company,
			"valid_to": ("is", "not set"),
		},
		pluck="name",
	):
		if frappe.db.get_value("Employee Device", name, "employee") == employee:
			# Already theirs. Nothing to do, and no duplicate row.
			return
		frappe.db.set_value("Employee Device", name, "valid_to", today)

	frappe.get_doc(
		{
			"doctype": "Employee Device",
			"custom_company": company,
			"employee": employee,
			"device_key": device_key,
			"valid_from": today,
			"paired_by": frappe.session.user,
		}
	).insert(ignore_permissions=True)


def _reporting_till(branch, company):
	"""A till at this branch that is actually alive right now.

	A pairing needs eyes in the room. Choosing the most recently heard-from till means
	the QR points at one that can answer, rather than one that was switched off an hour
	ago.
	"""
	rows = frappe.get_all(
		"Presence Till",
		filters={"branch": branch, "custom_company": company, "status": "Active"},
		fields=["name", "local_url", "last_seen"],
		order_by="last_seen desc",
		limit=1,
	)
	return rows[0] if rows else None
