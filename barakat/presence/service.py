"""Joins the pure engine to the database.

The engine decides; this module remembers. It loads a branch's state out of
`Presence Live Device`, hands it to `engine.apply_report` or `engine.tick`, and writes
back whatever the engine decided.

Nothing here makes a decision. If you find yourself adding a threshold, a timer or a
comparison to this file, it belongs in `engine.py`, where it can be tested with a
timeline and no database.
"""

from datetime import timedelta

import frappe
from frappe.utils import get_datetime, now_datetime

from barakat.presence import engine
from barakat.presence.mode import settings_for

LIVE = "Presence Live Device"


def load_state(branch, company, warmup_s):
	"""Rebuild the engine's view of a branch from what is stored.

	Rebuilt per call rather than cached: a cache that is lost mid-shift would either
	invent a mass departure or silently stop producing them, and neither failure is
	visible until payday.
	"""
	state = engine.BranchState()

	for row in frappe.get_all(
		LIVE,
		filters={"branch": branch, "custom_company": company},
		fields=["device_key", "last_seen", "present"],
		limit_page_length=0,
	):
		state.last_seen[row.device_key] = get_datetime(row.last_seen)
		if row.present:
			state.present.add(row.device_key)

	for till in frappe.get_all(
		"Presence Till",
		filters={"branch": branch, "custom_company": company, "status": "Active"},
		fields=["name", "last_seen", "is_settled", "is_blind"],
		limit_page_length=0,
	):
		if not till.last_seen:
			continue
		# Whether a watcher is past its warm-up is something ONLY the watcher knows -
		# it restarts independently of this record, so the record's age says nothing.
		# A blind watcher (one that cannot even see the router) is also not evidence:
		# it sees nothing because it is broken, not because the shop is empty.
		settled = bool(till.is_settled) and not till.is_blind
		state.till_last_report[till.name] = (get_datetime(till.last_seen), settled)

	return state


def save_devices(branch, company, state, touched):
	"""Persist the live view for the devices this call actually changed."""
	for device_key in touched:
		last_seen = state.last_seen.get(device_key)
		if not last_seen:
			continue
		name = f"{branch}::{device_key}"
		if frappe.db.exists(LIVE, name):
			frappe.db.set_value(
				LIVE,
				name,
				{"last_seen": last_seen, "present": 1 if device_key in state.present else 0},
				update_modified=False,
			)
		else:
			frappe.get_doc(
				{
					"doctype": LIVE,
					"custom_company": company,
					"branch": branch,
					"device_key": device_key,
					"last_seen": last_seen,
					"present": 1 if device_key in state.present else 0,
				}
			).insert(ignore_permissions=True)


def record_sightings(till, decisions):
	"""Write the raw appeared/gone rows.

	Direct SQL, not the document layer: that layer runs hooks, versioning and permission
	checks per row, which is right for anything a human touches and far too heavy for a
	constant stream. Only CHANGES are written - a branch storing every sweep would write
	about 43,000 rows a day instead of roughly 60.
	"""
	if not decisions:
		return

	rows = [
		(
			frappe.generate_hash(length=10),
			till.custom_company,
			till.branch,
			till.name,
			decision.device_key,
			"appeared" if decision.kind == engine.ARRIVED else "gone",
			decision.at,
		)
		for decision in decisions
	]

	frappe.db.sql(
		"""INSERT INTO `tabPresence Sighting`
			(name, owner, creation, modified, modified_by, docstatus, idx,
			 custom_company, branch, till, device_key, event, server_time)
			VALUES """
		+ ", ".join(
			["(%s, 'Administrator', NOW(), NOW(), 'Administrator', 0, 0, %s, %s, %s, %s, %s, %s)"]
			* len(rows)
		),
		[value for row in rows for value in row],
	)


def employee_for(device_key, company, when):
	"""Whose device is this, at that moment? None if nobody's.

	`when` is a full datetime, not a date. That matters for the closing day: `valid_to`
	is a date, so a pairing ended at 14:00 would go on counting until midnight, and
	"stop counting" has to mean now. `closed_at` carries the moment and this excludes
	anything closed at or before `when`.

	Only pairings that were open AT THE TIME count. A pairing is closed with a date
	rather than deleted precisely so that a January session still resolves to whoever
	held the phone in January — and so that unpairing at 14:00 leaves this morning's
	session still belonging to them.
	"""
	day = when.date() if hasattr(when, "date") else when

	rows = frappe.get_all(
		"Employee Device",
		filters={
			"device_key": device_key,
			"custom_company": company,
			"valid_from": ("<=", day),
		},
		or_filters=[["valid_to", "is", "not set"], ["valid_to", ">=", day]],
		fields=["employee", "closed_at"],
		limit=2,
	)
	for row in rows:
		if row.closed_at and row.closed_at <= when:
			continue
		return row.employee
	return None


def apply_decisions(till, decisions):
	"""Turn engine decisions into sessions for the people they belong to."""
	for decision in decisions:
		# The full moment, not the day: a pairing ended this afternoon must stop counting
		# this afternoon, while this morning's sightings still belong to whoever held it.
		employee = employee_for(decision.device_key, till.custom_company, decision.at)
		if not employee:
			# An unpaired device. Recorded as a sighting, belongs to nobody, and is
			# how the pairing screen finds a phone in the first place.
			continue

		if decision.kind == engine.ARRIVED:
			_open_session(till, employee, decision.at)
		else:
			_close_session(till, employee, decision.at)


def _open_session(till, employee, when):
	existing = frappe.db.exists(
		"Presence Session",
		{
			"employee": employee,
			"branch": till.branch,
			"custom_company": till.custom_company,
			"state": "Open",
		},
	)
	if existing:
		return

	frappe.get_doc(
		{
			"doctype": "Presence Session",
			"custom_company": till.custom_company,
			"branch": till.branch,
			"employee": employee,
			"in_time": when,
			"state": "Open",
		}
	).insert(ignore_permissions=True)


def _close_session(till, employee, when):
	name = frappe.db.exists(
		"Presence Session",
		{
			"employee": employee,
			"branch": till.branch,
			"custom_company": till.custom_company,
			"state": "Open",
		},
	)
	if not name:
		return

	# `when` is the moment the device actually vanished, not the moment the wait ran
	# out. Somebody who left at 17:12 left at 17:12.
	frappe.db.set_value(
		"Presence Session", name, {"out_time": when, "state": "Closed"}
	)


def ingest(till, devices, seen_at, settled=True, blind=False):
	"""One watcher report, end to end. Returns the decisions taken.

	`settled` and `blind` come from the watcher, because only the watcher knows when it
	started and whether it can see its own router. Both mean the same thing to the
	engine: this view is not evidence that anybody left.
	"""
	settings = settings_for(till.custom_company)
	state = load_state(till.branch, till.custom_company, settings["warmup_s"])

	report = engine.Report(
		till=till.name,
		at=seen_at,
		devices=frozenset(devices),
		settled=bool(settled) and not blind,
	)
	decisions = engine.apply_report(state, report)

	save_devices(till.branch, till.custom_company, state, devices)
	record_sightings(till, decisions)
	apply_decisions(till, decisions)
	return decisions


def sweep(branch, company):
	"""Age out devices nobody has seen for the wait. Returns the departures.

	Nothing else calls `tick`, so without the scheduled job that calls this, a shop
	would fill up with people who arrived and never left.
	"""
	settings = settings_for(company)
	state = load_state(branch, company, settings["warmup_s"])

	decisions = engine.tick(
		state,
		now_datetime(),
		timedelta(minutes=settings["departure_wait_minutes"]),
		timedelta(seconds=max(settings["heartbeat_s"] * 5, 300)),
	)
	if not decisions:
		return []

	save_devices(branch, company, state, [d.device_key for d in decisions])

	till = _any_till(branch, company)
	if till:
		record_sightings(till, decisions)
		apply_decisions(till, decisions)
	return decisions


def _any_till(branch, company):
	name = frappe.db.get_value(
		"Presence Till",
		{"branch": branch, "custom_company": company, "status": "Active"},
	)
	return frappe.get_doc("Presence Till", name) if name else None
