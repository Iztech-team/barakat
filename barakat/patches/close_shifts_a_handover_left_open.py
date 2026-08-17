"""Close shifts left open by a phone that was handed to somebody else.

Until this release, re-pairing a device moved the pairing and stopped there. The person
who had held it kept an OPEN `Presence Session`, and nothing could ever close it: a
departure is noticed when the device disappears from the wifi, and that device now
resolves to its new owner, so the sweep credits the departure to them and the old shift
hangs open indefinitely. On screen it reads as somebody still at work — on qa-test, since
09:44 the previous morning.

Only sessions nobody can rescue are touched: none of the person's remaining devices may
be LIVE. If another of their phones is on the branch wifi they are evidently here, and
the ordinary departure logic can still watch that one leave — the same rule
`pairing._close_dangling_session` applies live.

Live, not merely paired. Those came apart on test, which is why this patch was rewritten:
a manager handed one phone over and kept a pairing for a second phone that had never
been on the wifi at all. "Do they hold another pairing" said yes and left the shift
open; the phone it pointed at could never be seen to arrive OR leave, so no departure
was ever detected and nothing could close it.

The moment used is the handover itself, recovered from the NEXT pairing of that device:
its `creation` is when the phone changed hands, which is the last instant the old owner
could honestly be credited with. Where that cannot be established the session is left
alone rather than closed at a guessed time — a wrong end time is worse than a visible
open one, because it looks settled.
"""

import frappe


def execute():
	if not frappe.db.table_exists("Presence Session"):
		return

	open_sessions = frappe.get_all(
		"Presence Session",
		filters={"state": "Open"},
		fields=["name", "employee", "custom_company", "device_key", "in_time"],
	)
	if not open_sessions:
		return

	closed = 0
	for session in open_sessions:
		if not session.device_key:
			continue

		# Anyone still visible through another phone is not orphaned.
		held = frappe.get_all(
			"Employee Device",
			filters={
				"employee": session.employee,
				"custom_company": session.custom_company,
				"valid_to": ("is", "not set"),
			},
			pluck="device_key",
		)
		if held and frappe.db.count(
			"Presence Live Device",
			{
				"device_key": ("in", held),
				"custom_company": session.custom_company,
				"present": 1,
			},
		):
			continue

		handover = _handover_moment(session)
		if not handover or handover <= session.in_time:
			continue

		frappe.db.set_value(
			"Presence Session",
			session.name,
			{"out_time": handover, "state": "Closed"},
			update_modified=False,
		)
		closed += 1

	if closed:
		frappe.db.commit()
		print(f"presence: closed {closed} shift(s) orphaned by a device handover")


def _handover_moment(session):
	"""When this device stopped being theirs, to the second.

	`valid_to` is a Date and would only say "some time that day". The next pairing's
	`creation` is the actual instant the phone changed hands.
	"""
	rows = frappe.get_all(
		"Employee Device",
		filters={
			"device_key": session.device_key,
			"custom_company": session.custom_company,
			"employee": ("!=", session.employee),
		},
		fields=["creation"],
		order_by="creation asc",
		limit_page_length=0,
	)
	for row in rows:
		if row.creation > session.in_time:
			return row.creation
	return None
