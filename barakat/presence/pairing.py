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

One phone, one person - so a phone that scans while it still belongs to somebody else is
a HANDOVER, and step 4 stops. The pairing is not made, the previous owner keeps their
phone and their open shift, and the session is parked at `Needs Confirmation` until the
manager answers on their own screen. It used to go through silently: the first person was
unpaired, their shift was closed, and the only trace was a row in a list nobody was
looking at. Nothing on either screen said a word.

The question can only be asked at this point in the flow, and that is worth saying plainly
because it looks like it belongs on the button. At the moment the manager presses Pair,
nobody knows which phone is coming - that is the entire reason the QR exists. The device
is not known until it scans, so neither is the clash.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from barakat.presence import keys, service
from barakat.presence.mode import is_wifi_mode, settings_for

SESSION = "Presence Pairing Session"

# The states a pairing window can still move out of. Anything else is finished, and a
# finished window is never revived - a second scan means a second code.
LIVE_STATES = ("Waiting", "Needs Confirmation")


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
	#
	# `Needs Confirmation` counts as open. A window waiting on a manager's answer is one
	# they can still say yes to, so leaving it behind while minting a fresh code would let
	# a takeover they had walked away from be confirmed minutes later, out of a dialog
	# they had already replaced.
	frappe.db.delete(SESSION, {"employee": employee, "state": ("in", LIVE_STATES)})

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
	"""Has the phone scanned yet? Polled by the manager's screen.

	Read permission on the person being paired, because this now answers with a SECOND
	person's name - whoever holds the phone. The code is a short-lived hash and this is
	only ever polled by the screen that minted it, but "hard to guess" is not a permission
	check, and what it hands out stopped being harmless the day it started naming staff.
	"""
	row = frappe.db.get_value(
		SESSION,
		{"code": code},
		[
			"name",
			"state",
			"employee",
			"device_key",
			"conflict_employee",
			"branch",
			"expires_at",
			"custom_company",
		],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Unknown pairing code."), frappe.DoesNotExistError)

	frappe.get_doc("Employee", row.employee).check_permission("read")

	# Both live states expire. `Needs Confirmation` especially: a window nothing timed out
	# would sit there until somebody happened to open the screen again, and they would be
	# answering for a scan from last week against a room they cannot see.
	if row.state in LIVE_STATES and row.expires_at < now_datetime():
		frappe.db.set_value(SESSION, row.name, "state", "Expired")
		row.state = "Expired"

	# Counted on the server. The browser's clock is not the site's, and the whole point of
	# the number is that it agrees with the moment `confirm` starts refusing.
	remaining = 0
	if row.state in LIVE_STATES:
		remaining = max(0, int((row.expires_at - now_datetime()).total_seconds()))

	return {
		"state": row.state,
		"employee": row.employee,
		"deviceKey": row.device_key,
		"shownAs": (row.device_key or "")[-4:],
		"expiresInS": remaining,
		# Not only while the question is open. A window that was ANSWERED still has to be
		# able to say who it was about - the screen that says "nothing changed, that
		# phone still counts for X" reads this after the answer, and gated on the live
		# state it named nobody at all. `_conflict_payload` returns None on a window that
		# never had a clash, so an ordinary pairing is unaffected.
		"conflict": _conflict_payload(row),
	}


def _conflict_payload(row):
	"""Who holds this phone, and what taking it away from them would end.

	`onTheClock` is the part a manager actually decides on. Moving a phone closes the
	previous owner's open session, and somebody told only "this is Ahmad's phone" has not
	been told that pressing the button also sends Ahmad home in the record.

	Names, not ids. `HR-EMP-00374` is not a person to anybody standing at a till.
	"""
	employee = row.conflict_employee
	if not employee:
		return None

	name, branch = frappe.db.get_value("Employee", employee, ["employee_name", "branch"]) or (None, None)

	return {
		"employee": employee,
		"employeeName": name or employee,
		"branch": branch,
		"onTheClock": bool(
			frappe.db.exists(
				"Presence Session",
				{
					"employee": employee,
					"custom_company": row.custom_company,
					"state": "Open",
				},
			)
		),
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
		[
			"name",
			"state",
			"employee",
			"branch",
			"custom_company",
			"device_key",
			"expires_at",
		],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Unknown pairing code."), frappe.DoesNotExistError)

	if row.custom_company != till.custom_company or row.branch != till.branch:
		# The code belongs to a different branch. A till may only answer for its own.
		frappe.throw(_("That code is not for this branch."), frappe.PermissionError)

	device_key = str(device_key).strip().lower()[:64]
	if not device_key:
		frappe.throw(_("No device was identified."))

	# The same phone asking again while its manager is still deciding.
	#
	# Somebody who has just been told to wait will refresh the page - that is what
	# people do - and the ordinary answer here is "that code has already been used",
	# a red cross that reads as a fault. Say the same thing as the first time instead.
	# The window is NOT extended: a phone must not be able to keep the question alive
	# by asking, or a manager who walked away leaves an answerable takeover open all
	# afternoon. A DIFFERENT phone gets the ordinary refusal below.
	if (
		row.state == "Needs Confirmation"
		and row.device_key == device_key
		and row.expires_at >= now_datetime()
	):
		return _pending_reply()

	if row.state != "Waiting":
		frappe.throw(_("That code has already been used."))
	if row.expires_at < now_datetime():
		frappe.db.set_value(SESSION, row.name, "state", "Expired")
		frappe.throw(_("That code has expired."))

	# Whose phone is this ALREADY? Same company only - a device paired at another tenant
	# is not this shop's business, and naming their staff here would be the Contact and
	# Item Price leak again, through a door a till can open.
	holder = _current_owner(device_key, row.custom_company)
	if holder and holder != row.employee:
		return _hold_for_confirmation(row, till, device_key, holder)

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


def _hold_for_confirmation(row, till, device_key, holder):
	"""Park the window and change NOTHING. A manager has to answer first.

	Returned, not thrown, and the difference is not stylistic. A `frappe.throw` rolls the
	transaction back, which would throw away the very row that records the question - the
	window would go back to Waiting and the manager would keep watching a QR for a phone
	that had already scanned. A throw is also what the phone draws as a red cross, and
	nothing here has failed.

	The reply to the phone names nobody. That page is reachable by anyone who can guess a
	URL on the shop wifi, and "this phone belongs to Ahmad" is not a sentence it should be
	able to be made to say.

	The clock restarts. What ran out was the time to SCAN, which has now happened; the
	manager's answer is a separate wait, and inheriting the stub of the first one would put
	a dialog on screen that expires while it is being read.
	"""
	timeout = settings_for(row.custom_company)["pairing_timeout_s"]

	frappe.db.set_value(
		SESSION,
		row.name,
		{
			"state": "Needs Confirmation",
			"device_key": device_key,
			"conflict_employee": holder,
			"till": till.name,
			"held_at": now_datetime(),
			"expires_at": add_to_date(now_datetime(), seconds=timeout),
		},
	)
	return _pending_reply()


def _pending_reply():
	"""What the phone is told, and it is the same however many times it asks."""
	return {
		"ok": False,
		"needs_confirmation": True,
		"message": _("Almost there. Ask your manager to confirm on their screen."),
	}


def _current_owner(device_key, company):
	"""The employee holding this device right now, or None.

	Open rows only. A closed pairing is history and must never stand in the way of a new
	owner - the same rule `Employee Device.validate` enforces on the way in.
	"""
	rows = frappe.get_all(
		"Employee Device",
		filters={
			"device_key": device_key,
			"custom_company": company,
			"valid_to": ("is", "not set"),
		},
		pluck="employee",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def confirm(code):
	"""Yes, move that phone. Manager work, and the only way a takeover ever happens.

	Everything is checked again here rather than trusted from the moment of the scan. A
	minute passed while somebody read a dialog, and in that minute the phone can have been
	unpaired, handed to a third person, or the branch's last till suspended. The manager
	answered a question about the shop as it was; this refuses to act on the answer if the
	shop has moved on.
	"""
	row = _live_session(code, "Needs Confirmation")

	# A till holds a key that can `claim`. It must never be able to answer the question
	# `claim` is not allowed to answer for itself, or a stolen till key could walk a phone
	# off one person and onto another.
	#
	# Before the permission check, not after. A till account has no write on an Employee
	# today, so behind that check this would never run and the rule would be stated by
	# nothing - which is exactly the arrangement that fails quietly the day somebody
	# widens what a till may write.
	_refuse_a_till()
	frappe.get_doc("Employee", row.employee).check_permission("write")

	if not is_wifi_mode(row.custom_company):
		frappe.throw(_("Wifi presence is not enabled for this company."))

	holder = _current_owner(row.device_key, row.custom_company)
	if holder == row.employee:
		# Somebody got there first - a second manager, or the phone scanning again after
		# the first confirmation. The end state asked for is the end state we are in.
		_settle(row)
		return {"ok": True, "employee": row.employee, "already": True}
	if holder is not None and holder != row.conflict_employee:
		# The phone changed hands under the manager, or was unpaired and given to somebody
		# else entirely. The dialog they answered named the wrong person, so their answer
		# cannot be applied to this.
		frappe.throw(_("That phone has changed hands since you were asked. Start the pairing again."))

	# `holder is None` goes through on purpose: the clash was resolved while they were
	# deciding - somebody pressed Stop counting - and a takeover of nobody is an ordinary
	# pairing. Refusing would make a manager redo a scan to reach a state nothing objects
	# to.
	note = None
	if holder:
		note = _("Handed over to {0}, confirmed by {1}.").format(row.employee, frappe.session.user)

	_pair(row.employee, row.device_key, row.custom_company, note=note)

	till = _live_till(row)
	if till:
		_start_session_if_already_here(till, row.employee, row.device_key)

	_settle(row)
	return {"ok": True, "employee": row.employee, "took_over_from": holder}


@frappe.whitelist()
def cancel(code):
	"""No, leave it. Manager work.

	Closes the window rather than leaving it to time out. Two minutes of a live code after
	somebody has said no is two minutes in which the same phone can scan again and put the
	question back on a screen nobody is watching any more.
	"""
	row = _live_session(code, "Needs Confirmation")
	_refuse_a_till()
	frappe.get_doc("Employee", row.employee).check_permission("write")

	frappe.db.set_value(SESSION, row.name, "state", "Cancelled")
	return {"ok": True}


def _refuse_a_till():
	"""Neither half of the answer is a till's to give. See `confirm` for why."""
	if keys.till_for_current_user():
		frappe.throw(_("A till may not answer a handover."), frappe.PermissionError)


def _live_session(code, expected_state):
	"""The window behind a code, or a refusal that says which of the two went wrong."""
	row = frappe.db.get_value(
		SESSION,
		{"code": code},
		[
			"name",
			"state",
			"employee",
			"branch",
			"custom_company",
			"device_key",
			"conflict_employee",
			"till",
			"expires_at",
		],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Unknown pairing code."), frappe.DoesNotExistError)
	if row.state != expected_state:
		frappe.throw(_("That code has already been answered."))
	if row.expires_at < now_datetime():
		frappe.db.set_value(SESSION, row.name, "state", "Expired")
		frappe.throw(_("That code has expired. Start the pairing again."))
	return row


def _live_till(row):
	"""A till at this branch that is still Active, or None.

	Not necessarily the till that took the scan - it may have been suspended in the
	meantime, and suspending the last till at a branch closes every open shift there on
	purpose. Opening a fresh one behind that would create a session with nothing left in
	the building able to close it, which is the failure this feature spends the most care
	avoiding.

	Only `branch` and `custom_company` are ever read off it, so the branch's own row is as
	good as the one that answered the QR.
	"""
	names = frappe.get_all(
		"Presence Till",
		filters={
			"branch": row.branch,
			"custom_company": row.custom_company,
			"status": "Active",
		},
		pluck="name",
		limit=1,
	)
	if not names:
		return None
	return frappe._dict(name=names[0], branch=row.branch, custom_company=row.custom_company)


def _settle(row):
	"""Mark the window finished.

	`conflict_employee` is left exactly as it was written when the question was asked. It
	records WHY this window was held, which is the half that cannot be reconstructed
	afterwards - what was decided is readable off the Employee Device rows either way.
	"""
	frappe.db.set_value(
		SESSION,
		row.name,
		{"state": "Claimed", "claimed_at": now_datetime()},
	)


def _pair(employee, device_key, company, note=None):
	"""Attach the device, closing whoever held it before.

	Nothing reaches this with somebody else's phone unless a manager has said so out loud.
	`claim` parks a clash at `Needs Confirmation` and `confirm` is what calls this - which
	is why the takeover is still written here in full rather than being made impossible:
	the handover is legitimate, it just is not silent any more.

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
			"Employee Device",
			name,
			{
				"valid_to": today,
				"closed_at": now,
				# Why this ended, on the row it ended. A closed pairing next to an open
				# one for the same phone reads as a mystery six months later; a line
				# saying who took it and who allowed it reads as a decision.
				**({"notes": note} if note else {}),
			},
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
	"""Close an open session only if nothing can still SEE this person.

	If another of their phones is on the branch wifi right now they are evidently here,
	and the ordinary departure logic can watch that one leave. Closing on their behalf
	would clock them out while they are standing at the till.

	The test is whether a device is LIVE, not whether one is paired. Those came apart
	the moment somebody held a second pairing they were not carrying: the phone was
	never on the wifi, so it could never be seen to leave, so no departure was ever
	detected and the shift stayed open with nothing able to close it. Seen on test —
	a manager handed one phone to a colleague, kept a second pairing for a phone that
	was not in the building, and both of them showed as present for the same
	three quarters of an hour.
	"""
	held = frappe.get_all(
		"Employee Device",
		filters={
			"employee": employee,
			"custom_company": company,
			"valid_to": ("is", "not set"),
		},
		pluck="device_key",
	)
	if held and frappe.db.count(
		"Presence Live Device",
		{"device_key": ("in", held), "custom_company": company, "present": 1},
	):
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
