"""The only two doors into presence.

`request_join` is called by the POS, under the login that till already has. It creates a
pending record and nothing else - a key is never issued without a human approving it,
so any POS login can ask to join and none can let itself in.

`report` is called by the watcher, under its own key. It writes and never reads: there
is no route here that returns presence, staff, other branches or its own history, so a
stolen key holds nothing worth having.

Everything either method knows about scope comes from the credential. The request body
names no company, no branch and no till, because a caller that can name its own scope
can name somebody else's. That is the rule that failed once on Contact and Item Price.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime

from barakat.presence import keys, service, spans
from barakat.presence.mode import is_wifi_mode, settings_for

MAX_BODY_DEVICES = 512
MAX_CLOCK_DRIFT_S = 300


@frappe.whitelist()
def request_join(pos_profile, machine_name=None, machine_fingerprint=None):
	"""Ask to be let in. Called by the POS under its own session.

	Returns `pending` until a manager approves, then hands the key over exactly once.
	There is no bootstrap mode and no weaker entrance: the till is already authenticated
	as itself, which is what removes the need for one.
	"""
	if not pos_profile:
		frappe.throw(_("pos_profile is required."))

	name = frappe.db.exists("Presence Till", {"pos_profile": pos_profile})
	if name:
		till = frappe.get_doc("Presence Till", name)
	else:
		till = frappe.get_doc(
			{
				"doctype": "Presence Till",
				"pos_profile": pos_profile,
				"machine_name": machine_name,
				"machine_fingerprint": machine_fingerprint,
				"status": "Pending",
			}
		)
		till.insert(ignore_permissions=True)

	if not is_wifi_mode(till.custom_company):
		return {"status": "off"}

	if till.status in ("Suspended", "Retired"):
		return {"status": till.status.lower()}

	if till.status == "Pending":
		return {"status": "pending"}

	if till.key_issued_at:
		# Already collected. Nothing on the server can hand it out again - Frappe keeps
		# only a hash of the secret - so a lost key is reissued by a manager, never
		# recovered.
		#
		# And the asking itself is the news. A till only reaches this endpoint when it
		# has nothing stored locally, so a till we have already issued a key to is a
		# till whose key is GONE - reimaged, reinstalled, profile wiped. Before this was
		# recorded, that till sat in the Admin Panel as Active and simply never reported,
		# indistinguishable from a branch with a network fault, and every minute of its
		# staff's attendance was lost while somebody looked at the router.
		# WHO asked matters as much as that somebody did. A till is keyed by POS Profile,
		# so a second machine selling under the same profile is the same till — and after
		# a reissue the new key goes to whichever machine asks first. Recording the name
		# lets a manager see that the computer asking is not the computer the key was
		# issued to, before they hand out another one.
		#
		# Never at the cost of the answer, though. This is a note for a manager; being
		# let in is the job, and a diagnostic that can refuse entry has its priorities
		# backwards. A bench migrates its sites one at a time, so new code routinely runs
		# for a few minutes against a schema that has not caught up — an ordinary window,
		# not an exceptional one, and reason enough on its own for this write to be
		# unable to take the endpoint down with it.
		try:
			frappe.db.set_value(
				"Presence Till",
				till.name,
				{"asked_again_at": now_datetime(), "asked_again_by": machine_name},
			)
		except Exception:
			frappe.log_error(
				title="presence: could not record a re-ask",
				message=frappe.get_traceback(),
			)
		return {"status": "active"}

	credentials = keys.issue_key(till)
	# Stamped with the machine that COLLECTED it, not the one that first asked. A till
	# record can be created by one computer and approved days later, by which time the
	# hardware may have been replaced; the key belongs to whatever machine is holding it
	# now, and that is what a later mismatch has to be measured against.
	frappe.db.set_value(
		"Presence Till",
		till.name,
		{
			"api_user": credentials["user"],
			"key_issued_at": now_datetime(),
			"machine_name": machine_name or till.machine_name,
			"machine_fingerprint": machine_fingerprint or till.machine_fingerprint,
		},
	)
	return {
		"status": "approved",
		"till": till.name,
		"branch": till.branch,
		"api_key": credentials["api_key"],
		"api_secret": credentials["api_secret"],
		"settings": _watcher_settings(till.custom_company),
	}


@frappe.whitelist()
def approve(till):
	"""Approve a pending till. Manager work, gated on `settings: write`.

	The key is not returned here. It is collected by the till itself on its next
	`request_join`, so the credential never passes through a browser, a clipboard or a
	person.
	"""
	doc = frappe.get_doc("Presence Till", till)
	doc.check_permission("write")

	if doc.status != "Pending":
		frappe.throw(_("Till {0} is not pending approval.").format(till))

	frappe.db.set_value(
		"Presence Till",
		doc.name,
		{
			"status": "Active",
			"approved_by": frappe.session.user,
			"approved_at": now_datetime(),
		},
	)
	return {"status": "active"}


@frappe.whitelist()
def suspend(till):
	"""Stop accepting this till at once. Its account is disabled with it.

	If it was the last pair of eyes at its branch, every open shift there is closed —
	see `_close_branch_sessions` for why that is not the same as a till going dark.
	"""
	doc = frappe.get_doc("Presence Till", till)
	doc.check_permission("write")

	keys.revoke(doc)
	frappe.db.set_value("Presence Till", doc.name, "status", "Suspended")
	closed = _close_branch_sessions(doc)
	return {"status": "suspended", "sessions_closed": closed}


def _close_branch_sessions(till):
	"""Close open shifts at a branch that has just lost its last watcher.

	The departure sweep deliberately refuses to send anybody home when no settled
	watcher has reported recently: a branch nobody can see is unreachable, not empty,
	and a till losing power must never clock out a whole shop. Suspending the last till
	inherits that protection and turns it into a trap — the shifts simply stay open, for
	ever, and nobody is told.

	The difference is that suspending is deliberate. The server knows exactly why the
	eyes went away, and it knows the moment they did, so it can say so instead of
	leaving a record that claims somebody is still at work a week later.

	`out_time` is NOW, not each person's last sighting. They may well still be standing
	in the shop; what ended at this instant is our ability to see them, and that is the
	honest thing to write down. Only when no other ACTIVE till remains — a branch with a
	second till still has eyes, and closing on its behalf would clock people out while
	it can see them perfectly well.
	"""
	others = frappe.db.count(
		"Presence Till",
		{
			"branch": till.branch,
			"custom_company": till.custom_company,
			"status": "Active",
			"name": ("!=", till.name),
		},
	)
	if others:
		return 0

	now = now_datetime()
	names = frappe.get_all(
		"Presence Session",
		filters={
			"branch": till.branch,
			"custom_company": till.custom_company,
			"state": "Open",
		},
		pluck="name",
	)
	for session in names:
		frappe.db.set_value(
			"Presence Session", session, {"out_time": now, "state": "Closed"}
		)
	return len(names)


@frappe.whitelist()
def reactivate(till):
	"""Bring a suspended till back. Manager work.

	Suspending is not a pause, so this is not simply its opposite: the account was
	disabled and the till has been refused ever since. It comes back with a NEW key and
	the old one dead, because the reason a till gets suspended is usually that somebody
	is not sure where it is — and a machine that has been out of the shop for a week
	should not walk back in on the credential it left with.

	`approve` cannot do this job: it refuses anything that is not Pending, and it would
	leave the disabled account disabled, so the till would show Active and still be
	turned away on every report.
	"""
	doc = frappe.get_doc("Presence Till", till)
	doc.check_permission("write")

	if doc.status != "Suspended":
		frappe.throw(_("Till {0} is not suspended.").format(till))

	email = keys.user_name_for(doc)
	if frappe.db.exists("User", email):
		frappe.db.set_value("User", email, "enabled", 1)

	# `asked_again_at` goes with the key. It records a till complaining that it has
	# nothing to report with; having just been given a fresh chance to collect one, the
	# complaint is answered, and leaving it set would keep the Admin Panel asking a
	# manager to fix something they have already fixed.
	frappe.db.set_value(
		"Presence Till",
		doc.name,
		{
			"status": "Active",
			"key_issued_at": None,
			"asked_again_at": None,
			"asked_again_by": None,
		},
	)
	return {"status": "awaiting-collection"}


@frappe.whitelist()
def reissue(till):
	"""For a reimaged or replaced PC. The old key dies the moment this is called."""
	doc = frappe.get_doc("Presence Till", till)
	doc.check_permission("write")

	# The old key has to actually DIE, which this promised and did not do.
	#
	# Clearing `key_issued_at` only makes the server forget it handed one out; the
	# credential itself kept working, because the account was never disabled. So a till
	# reissued after being stolen or reimaged carried on reporting on the old key, and
	# the Admin Panel sat on "Collecting its key" for ever — the till had no reason to
	# ask for a new one, since the one it held was fine.
	#
	# Revoking is safe here: the next `request_join` re-enables the account and rotates
	# the secret, so the till collects a fresh key and carries on by itself.
	keys.revoke(doc)

	frappe.db.set_value(
		"Presence Till",
		doc.name,
		{
			"key_issued_at": None,
			"status": "Active",
			"asked_again_at": None,
			"asked_again_by": None,
		},
	)
	return {"status": "awaiting-collection"}


@frappe.whitelist()
def report(
	devices=None,
	seq=None,
	sent_at=None,
	watcher_version=None,
	health=None,
	local_url=None,
):
	"""One watcher's view of its branch. Writes; returns nothing readable.

	This method is the entire security boundary. The watcher's user holds no DocPerm on
	anything at all, so every check that matters happens here, starting with proving the
	caller is a live till before anything else is touched.
	"""
	till = keys.till_for_current_user()
	if not till:
		frappe.throw(_("Not a registered till."), frappe.PermissionError)

	if till.status != "Active":
		frappe.throw(_("This till is {0}.").format(till.status), frappe.PermissionError)

	if not is_wifi_mode(till.custom_company):
		frappe.throw(_("Wifi presence is not enabled."), frappe.PermissionError)

	devices = _clean_devices(devices, till)
	seq = _check_sequence(till, seq)
	drift = _clock_drift(sent_at)

	# The server stamps the time. A till's own clock is never used in a calculation -
	# shop PCs have wrong clocks routinely, and a wrong clock would corrupt attendance
	# silently rather than loudly.
	seen_at = now_datetime()

	health = frappe.parse_json(health) if isinstance(health, str) else (health or {})
	# The watcher decides both of these. It knows when it started, and it knows whether
	# it can see its own router; the server can know neither.
	blind = bool(health.get("blind"))
	settled = not bool(health.get("warming_up"))

	service.ingest(till, devices, seen_at, settled=settled, blind=blind)

	frappe.db.set_value(
		"Presence Till",
		till.name,
		{
			"last_seen": seen_at,
			"last_seq": seq,
			"watcher_version": watcher_version,
			"last_clock_drift_s": drift,
			"is_settled": 1 if settled else 0,
			"is_blind": 1 if blind else 0,
			# Where this till answers on the shop's own network. Only it can know -
			# the address is private to that building and changes with DHCP.
			"local_url": (local_url or "")[:140],
		},
		update_modified=False,
	)

	return {
		"ok": True,
		"server_time": str(seen_at),
		"next_heartbeat_s": settings_for(till.custom_company)["heartbeat_s"],
	}


# ---------------------------------------------------------------- guards


def _clean_devices(devices, till):
	if isinstance(devices, str):
		devices = frappe.parse_json(devices)
	devices = devices or []

	if len(devices) > MAX_BODY_DEVICES:
		# Alarmed rather than truncated. Silently dropping half a shop's devices would
		# read as "those people went home".
		frappe.throw(
			_("Report holds {0} devices, more than the {1} allowed.").format(
				len(devices), MAX_BODY_DEVICES
			)
		)

	cleaned = set()
	for entry in devices:
		key = entry.get("id") if isinstance(entry, dict) else entry
		if key:
			cleaned.add(str(key).strip().lower()[:64])
	return sorted(cleaned)


def _check_sequence(till, seq):
	"""Reject a replayed or duplicated report."""
	if seq is None:
		return till.last_seq

	seq = int(seq)
	if till.last_seq and seq <= till.last_seq:
		frappe.throw(_("Stale report."), frappe.DuplicateEntryError)
	return seq


def _clock_drift(sent_at):
	"""Recorded for support only. Never used to decide anything."""
	if not sent_at:
		return 0
	try:
		return int((now_datetime() - get_datetime(sent_at)).total_seconds())
	except Exception:
		return 0


def _watcher_settings(company):
	settings = settings_for(company)
	return {
		"sweep_interval_s": settings["sweep_interval_s"],
		"heartbeat_s": settings["heartbeat_s"],
		"warmup_s": settings["warmup_s"],
		"max_devices": settings["max_devices"],
	}


@frappe.whitelist()
def timeline(employee, from_date, to_date):
	"""Every session this person had, day by day, for a date range.

	Read-only and deliberately dumb: it hands back the raw stretches and lets the screen
	decide how to draw them. A session that starts before midnight and ends after it is
	returned once, on the day it STARTED, with its real times — splitting it here would
	invent two shifts out of one night.
	"""
	frappe.get_doc("Employee", employee).check_permission("read")

	rows = frappe.get_all(
		"Presence Session",
		filters={
			"employee": employee,
			"in_time": ("between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"]),
		},
		fields=[
			"name",
			"branch",
			"in_time",
			"out_time",
			"device_key",
			"state",
		],
		order_by="in_time asc",
		limit_page_length=0,
	)

	window_start = get_datetime(f"{from_date} 00:00:00")
	window_end = get_datetime(f"{to_date} 23:59:59")

	return {
		"sessions": [
			{
				"name": row.name,
				"branch": row.branch,
				"inTime": str(row.in_time),
				"outTime": str(row.out_time) if row.out_time else None,
				"deviceKey": row.device_key,
				"open": row.state == "Open",
			}
			for row in rows
		],
		"spans": _device_spans(employee, window_start, window_end),
		# Where the per-device detail runs out. Past this the sighting log has been
		# deleted and only the session block survives, so the screen can say so rather
		# than drawing an empty day that looks like an absence.
		"detailFrom": str(_detail_horizon(employee)),
	}


@frappe.whitelist()
def branch_day(branch, day):
	"""Everyone at one branch, across one day. The other axis of the same picture.

	`timeline` answers "one person, many days". A manager standing in a shop has the
	opposite question — "who was here today, and when" — and answering it by calling
	`timeline` once per employee would be one HTTP round trip per person on the payroll.

	Staff are listed even with nothing to show. An empty row says "assigned here, no
	sign of them today", which is a fact a manager can act on; omitting them makes
	absent and never-paired look identical, and telling those two apart is most of what
	this feature is for.
	"""
	company = frappe.db.get_value("Branch", branch, "custom_pos_company")
	if not company:
		frappe.throw(_("Branch {0} has no company set.").format(branch))
	frappe.get_doc("Branch", branch).check_permission("read")

	window_start = get_datetime(f"{day} 00:00:00")
	window_end = get_datetime(f"{day} 23:59:59")

	rows = frappe.get_all(
		"Presence Session",
		filters={
			"branch": branch,
			"custom_company": company,
			"in_time": ("between", [window_start, window_end]),
		},
		fields=["name", "employee", "in_time", "out_time", "device_key", "state"],
		order_by="in_time asc",
		limit_page_length=0,
	)

	sessions_by = {}
	for row in rows:
		sessions_by.setdefault(row.employee, []).append(
			{
				"name": row.name,
				"branch": branch,
				"inTime": str(row.in_time),
				"outTime": str(row.out_time) if row.out_time else None,
				"deviceKey": row.device_key,
				"open": row.state == "Open",
			}
		)

	# Assigned to the branch, PLUS anyone the day already has a session for. A person
	# moved to another branch last week still worked here on the day being looked at,
	# and a day that quietly dropped them would be a day that disagrees with itself.
	people = _staff_at_branch(branch, company)
	known = {row["employee"] for row in people}
	for employee in sessions_by:
		if employee not in known:
			people.append(
				{
					"employee": employee,
					"employeeName": frappe.db.get_value(
						"Employee", employee, "employee_name"
					)
					or employee,
					"stillHere": False,
				}
			)

	spans_by = _spans_by_employee(
		company,
		[row["employee"] for row in people],
		window_start,
		window_end,
		branch,
	)

	return {
		"branch": branch,
		"day": str(day),
		"staff": [
			{
				**row,
				"sessions": sessions_by.get(row["employee"], []),
				"spans": spans_by.get(row["employee"], []),
			}
			for row in people
		],
		"detailFrom": str(_detail_horizon_for_company(company)),
	}


def _staff_at_branch(branch, company):
	"""Everyone who works at this branch, by either route.

	ERPNext's Employee carries ONE `branch` link, so Barakat keeps the rest in the
	`POS Employee Branch` child table — a person can work at three shops and the native
	field can only name one. Asking only the native field would silently hide most of a
	multi-branch shop's staff.

	`get_list`, not `get_all`: this decides whose attendance a user may look at, and the
	tenant boundary is enforced by permissions rather than by the filters above.
	"""
	seen = {}
	for row in frappe.get_list(
		"Employee",
		filters={"company": company, "status": "Active", "branch": branch},
		fields=["name", "employee_name"],
		limit_page_length=0,
	):
		seen[row.name] = row.employee_name or row.name

	assigned = frappe.get_all(
		"POS Employee Branch",
		filters={"branch": branch, "parenttype": "Employee"},
		pluck="parent",
		limit_page_length=0,
	)
	if assigned:
		for row in frappe.get_list(
			"Employee",
			filters={
				"company": company,
				"status": "Active",
				"name": ("in", list(set(assigned))),
			},
			fields=["name", "employee_name"],
			limit_page_length=0,
		):
			seen[row.name] = row.employee_name or row.name

	return [
		{"employee": name, "employeeName": label, "stillHere": True}
		for name, label in sorted(seen.items(), key=lambda pair: pair[1].lower())
	]


def _spans_by_employee(company, employees, window_start, window_end, branch=None):
	"""Per-device stretches for a whole branch, in one pass over the log.

	Built once for every device rather than once per person: the sighting table is the
	biggest thing this feature writes, and a shop with thirty staff would otherwise scan
	it thirty times to draw one screen.
	"""
	if not employees:
		return {}

	ownership = _ownership_for(employees)
	if not ownership:
		return {}

	built = _spans_from_log(
		ownership, window_start, window_end, _seen_until(company, branch)
	)

	out = {}
	for span in built:
		# WHO held it at that moment, not who holds it now — the same rule the session
		# layer applies, so a phone handed over at noon splits between two people
		# instead of giving the whole day to whoever has it today.
		employee = service.employee_for(span.device_key, company, span.start)
		if not employee:
			continue
		out.setdefault(employee, []).append(
			{
				"deviceKey": span.device_key,
				"start": str(span.start),
				"end": str(span.end),
				"open": span.open_ended,
			}
		)
	return out


def _ownership_for(employees):
	"""Device → the stretches during which it belonged to one of these people."""
	rows = frappe.get_all(
		"Employee Device",
		filters={"employee": ("in", employees)},
		fields=["device_key", "valid_from", "valid_to", "closed_at", "creation"],
		limit_page_length=0,
	)
	ownership = {}
	for row in rows:
		start = _pairing_began(row)
		if row.closed_at:
			end = get_datetime(row.closed_at)
		elif row.valid_to:
			end = get_datetime(f"{row.valid_to} 23:59:59")
		else:
			end = None
		ownership.setdefault(row.device_key, []).append((start, end))
	return ownership


def _spans_from_log(ownership, window_start, window_end, seen_until=None):
	"""The shared half of every span query: read the log, fold it into stretches."""
	events = frappe.get_all(
		"Presence Sighting",
		filters={
			"device_key": ("in", list(ownership)),
			"server_time": ("between", [window_start, window_end]),
		},
		fields=["device_key", "event", "server_time"],
		order_by="server_time asc",
		limit_page_length=0,
	)
	return spans.build_spans(
		[(row.device_key, row.event, get_datetime(row.server_time)) for row in events],
		window_start,
		window_end,
		seen_until or now_datetime(),
		ownership=ownership,
	)


def _seen_until(company, branch=None):
	"""The last moment anything here could have seen a phone.

	A stretch with no `gone` used to be drawn to NOW, which is right only while somebody
	is still watching. When the tills stop reporting no departure is ever recorded — the
	sweep deliberately refuses to age anyone out of a branch nobody can see — so the
	stretch has no end, and drawing it to now made a block that grew a second every
	second and said "still here" for ever.

	Seen live on 2026-08-16: a phone last sighted at 14:50, its till last reporting at
	16:33, and the map still lengthening the block past 16:42 with the session beside it
	long since closed. Refreshing could not help; the answer was being computed that way
	every time.

	So an unfinished stretch stops where the evidence stops. While a branch is reporting
	this is within a heartbeat of now and nothing changes; once it goes quiet the block
	stops growing, which is exactly what "we cannot see the room any more" looks like.
	"""
	filters = {"custom_company": company}
	if branch:
		filters["branch"] = branch
	newest = frappe.db.get_value(
		"Presence Till", filters, "last_seen", order_by="last_seen desc"
	)
	now = now_datetime()
	if not newest:
		return now
	newest = get_datetime(newest)
	return newest if newest < now else now


def _detail_horizon_for_company(company):
	"""Where the sighting log runs out, for a whole company."""
	oldest = frappe.db.get_value(
		"Presence Sighting",
		{"custom_company": company},
		"server_time",
		order_by="server_time asc",
	)
	return oldest or now_datetime()


def _device_spans(employee, window_start, window_end):
	"""Per-device stretches, from the raw log, for one person.

	The session block cannot answer "which phone was here at 3pm": it records only the
	device that OPENED it and never changes, so a person carrying two gets one block
	coloured after whichever walked in first. The sighting log has appeared/gone per
	device, which is exactly the question, for as long as the log is kept.

	One person is the branch case with a list of one, so both go through the same
	ownership and folding code. Two implementations of "when did this device belong to
	somebody" would drift, and the half that drifted would be the one nobody was
	looking at.
	"""
	ownership = _ownership_for([employee])
	if not ownership:
		return []

	company = frappe.db.get_value("Employee", employee, "company")
	return [
		{
			"deviceKey": span.device_key,
			"start": str(span.start),
			"end": str(span.end),
			"open": span.open_ended,
		}
		for span in _spans_from_log(
			ownership, window_start, window_end, _seen_until(company)
		)
	]


def _pairing_began(row):
	"""The moment a phone became this person's, to the second.

	`valid_from` is a Date, so on its own it means midnight — and a phone paired at 20:25
	then draws the whole evening before it, hours when nothing on earth could say whose
	pocket it was in. The map showed a block starting at 19:45 beside a session that
	opened at 20:25, on the same screen, from the same person's data.

	Which was the more visible half of one mistake. Attendance deliberately starts at the
	pairing and not at first sighting — `_start_session_if_already_here` says why: time we
	could not attribute is time we must not invent — and drawing from midnight quietly
	invented exactly that, everywhere except in the totals.

	`creation` is when the pairing row was written, which is when somebody scanned the QR.
	Used only when the pairing began on the day it was recorded: a row deliberately
	back-dated to an earlier day means the whole of that day, which is what back-dating is
	for.

	The closing side already knew all this — it prefers `closed_at` over `valid_to` for
	precisely this reason. Only half the fix was ever in.
	"""
	midnight = get_datetime(f"{row.valid_from} 00:00:00")
	if not row.get("creation"):
		return midnight
	created = get_datetime(row.creation)
	return created if created.date() == getdate(row.valid_from) else midnight


def _detail_horizon(employee):
	"""The oldest moment the sighting log can still speak for.

	Read from the log itself rather than computed from the retention setting: the
	cleanup runs nightly, so the setting says what will eventually be true and the
	oldest surviving row says what IS true.
	"""
	company = frappe.db.get_value("Employee", employee, "company")
	oldest = frappe.db.get_value(
		"Presence Sighting",
		{"custom_company": company},
		"server_time",
		order_by="server_time asc",
	)
	return oldest or now_datetime()
