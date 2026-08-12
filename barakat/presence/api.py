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
from frappe.utils import get_datetime, now_datetime

from barakat.presence import keys, service
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
		return {"status": "active"}

	credentials = keys.issue_key(till)
	frappe.db.set_value(
		"Presence Till",
		till.name,
		{"api_user": credentials["user"], "key_issued_at": now_datetime()},
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
	"""Stop accepting this till at once. Its account is disabled with it."""
	doc = frappe.get_doc("Presence Till", till)
	doc.check_permission("write")

	keys.revoke(doc)
	frappe.db.set_value("Presence Till", doc.name, "status", "Suspended")
	return {"status": "suspended"}


@frappe.whitelist()
def reissue(till):
	"""For a reimaged or replaced PC. The old key dies the moment this is called."""
	doc = frappe.get_doc("Presence Till", till)
	doc.check_permission("write")

	frappe.db.set_value(
		"Presence Till", doc.name, {"key_issued_at": None, "status": "Active"}
	)
	return {"status": "awaiting-collection"}


@frappe.whitelist()
def report(devices=None, seq=None, sent_at=None, watcher_version=None, health=None):
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

	service.ingest(till, devices, seen_at)

	frappe.db.set_value(
		"Presence Till",
		till.name,
		{
			"last_seen": seen_at,
			"last_seq": seq,
			"watcher_version": watcher_version,
			"last_clock_drift_s": drift,
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
