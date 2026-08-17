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

from barakat.presence import keys, service
from barakat.presence.mode import is_wifi_mode, settings_for

SESSION = "Presence Pairing Session"


@frappe.whitelist()
def start(employee, branch):
	"""Open a pairing window for this person at this branch. Manager work."""
	# THE EMPLOYEE's company, and nothing else.
	#
	# This used to prefer `frappe.defaults.get_user_default("Company")`, which is the
	# operator's own convenience setting and has nothing to do with whose phone is being
	# paired. Worse, it falls back to the SITE-WIDE default when the user has none — so
	# on a site carrying twenty companies, a manager pairing a phone for an E2E Shop
	# employee resolved "Iztech Valley", found no wifi mode there, and was told the
	# feature was switched off for a company they were not looking at.
	#
	# It poisoned everything downstream too: the till lookup, the settings, and the
	# company stamped on the pairing request — which is then compared against the till's
	# at claim time. A pairing that had somehow got past the mode check would have been
	# refused later, for a reason nobody could have read.
	company = frappe.db.get_value("Employee", employee, "company")
	if not company:
		frappe.throw(_("Cannot tell which company this employee belongs to."))

	if not is_wifi_mode(company):
		frappe.throw(_("Wifi presence is not enabled for this company."))

	frappe.get_doc("Employee", employee).check_permission("write")

	alive = _reporting_tills(branch, company)
	if not alive:
		frappe.throw(
			_("No till at {0} is reporting right now, so nothing can see the phone.").format(
				branch
			)
		)

	# The freshest till that can actually ANSWER, not simply the freshest.
	#
	# A till reports an address only while its pairing server is listening, so a till
	# with none is one that cannot open the door. Taking the newest and giving up if it
	# happened to be that one made a branch's whole pairing depend on its busiest till,
	# while a perfectly healthy till stood beside it — for a job any till at the branch
	# can do, since they all watch the same network.
	till = next((row for row in alive if row.local_url), None)
	if not till:
		frappe.throw(
			_(
				"No till at {0} can accept phones right now ({1} reporting). Close the POS "
				"on one of those computers and open it again, then try once more. Its "
				"Settings screen shows the reason under Wifi watcher."
			).format(branch, len(alive))
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
	_start_session_if_already_here(till, row.employee, device_key)

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

	Handing a phone to somebody else is an UNPAIRING of the person who had it, and it
	has to do everything `unpair` does or it leaves the same two marks behind. Seen on
	qa-test: a phone scanned for one employee at 09:44:21 and for another at 09:45:13,
	which left the first with a session still open a day later.

	`closed_at` alongside the date, for the reason `unpair` spells out: `valid_to` is a
	Date, and the lookup asks whether the day falls inside the range, so without the
	moment BOTH pairings answer to this device for the rest of the handover day.

	And the previous owner's open session is closed here. Nothing else ever would: a
	departure is noticed when the device disappears, and this device now belongs to
	somebody else, so the sweep attributes it to the new owner and the old session hangs
	open for good — somebody showing as at work until a human spots it.
	"""
	now = now_datetime()
	today = now.date()

	for name in frappe.get_all(
		"Employee Device",
		filters={
			"device_key": device_key,
			"custom_company": company,
			"valid_to": ("is", "not set"),
		},
		pluck="name",
	):
		previous = frappe.db.get_value("Employee Device", name, "employee")
		if previous == employee:
			# Already theirs. Nothing to do, and no duplicate row.
			return
		frappe.db.set_value(
			"Employee Device", name, {"valid_to": today, "closed_at": now}
		)
		# After the pairing is closed, never before: this asks whether they have any
		# live device LEFT, and the one being taken away must not count itself.
		_close_dangling_session(previous, company, now)

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


def _start_session_if_already_here(till, employee, device_key):
	"""A phone paired while its owner is standing in the shop is here NOW.

	Arrival is a TRANSITION: a device that was not on the network and now is. A phone
	paired at the counter never makes that transition — it was already on the wifi when
	the QR was scanned, so it is already in the branch's present set and nothing new
	happens. Without this, the person's first session would not open until they left the
	shop and came back, which on the day they are enrolled means their whole shift goes
	unrecorded. That is precisely the day somebody is watching to see whether this works.

	The session starts NOW, not from when the device was first seen. They may well have
	been here for hours, but until this moment nothing could attribute that phone to a
	person, and attendance we could not attribute is attendance we should not invent.

	Only if the device is on the branch's live list. A phone paired from a back office,
	or over a QR sent by message, is not evidence its owner is in the shop.
	"""
	seen = frappe.db.exists(
		"Presence Live Device",
		{
			"device_key": device_key,
			"branch": till.branch,
			"custom_company": till.custom_company,
		},
	)
	if not seen:
		return False

	service._open_session(till, employee, now_datetime(), device_key)
	return True


def _reporting_tills(branch, company):
	"""Every till at this branch that is alive, freshest first.

	A pairing needs eyes in the room, and the caller wants the one most likely to
	answer — but it needs the REST of the list too, because the freshest till is not
	always the one that can open the door.
	"""
	return frappe.get_all(
		"Presence Till",
		filters={"branch": branch, "custom_company": company, "status": "Active"},
		fields=["name", "local_url", "last_seen"],
		order_by="last_seen desc",
		limit_page_length=0,
	)


@frappe.whitelist()
def unpair(name):
	"""End a pairing, effective now. Manager work.

	Two things have to happen, and the second is easy to miss.

	The pairing is closed with today's date AND the exact moment. The date alone leaves
	it counting until midnight, because `valid_to` is a date and the lookup asks whether
	today falls inside the range — so a phone handed back at 14:00 could still open a
	session at 16:00.

	And if that was the person's last live device while a session was open, the session
	is closed here. Nothing else would ever close it: a departure is detected by the
	device vanishing, and the moment the pairing is gone that device resolves to nobody,
	so the sweep skips it and the session hangs open for good. Somebody would show as at
	work until a human noticed.
	"""
	doc = frappe.get_doc("Employee Device", name)
	frappe.get_doc("Employee", doc.employee).check_permission("write")

	if doc.valid_to and doc.closed_at:
		return {"ok": True, "already": True}

	now = now_datetime()
	frappe.db.set_value(
		"Employee Device", doc.name, {"valid_to": now.date(), "closed_at": now}
	)

	closed = _close_dangling_session(doc.employee, doc.custom_company, now)
	return {"ok": True, "session_closed": closed}


def _close_dangling_session(employee, company, now):
	"""Close an open session only if this person has no live device left.

	If they still hold another paired phone they may genuinely still be here, and the
	ordinary departure logic can still see them leave through it. Closing on their
	behalf would clock them out while they are standing at the till.
	"""
	still_paired = frappe.db.count(
		"Employee Device",
		{
			"employee": employee,
			"custom_company": company,
			"valid_to": ("is", "not set"),
		},
	)
	if still_paired:
		return False

	names = frappe.get_all(
		"Presence Session",
		filters={"employee": employee, "custom_company": company, "state": "Open"},
		pluck="name",
	)
	for session in names:
		frappe.db.set_value(
			"Presence Session", session, {"out_time": now, "state": "Closed"}
		)
	return bool(names)
