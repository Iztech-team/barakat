"""Take employee POS PINs back out of the document history.

Employee has `track_changes` on, so every time somebody edited a POS PIN, Frappe wrote
the change into `tabVersion` as a plain diff — `["custom_pos_pin", "1234", "5678"]`. Both
values. Which means the history holds not only every employee's CURRENT PIN but every PIN
they have ever had, in cleartext, readable by anyone who can read Version, and untouched
by any amount of tightening applied to the Employee field itself.

Nothing needs that history. A PIN is a credential, not a business fact: knowing it changed
is occasionally useful, knowing what it changed from and to is only useful to somebody who
should not have it.

So the entry is not deleted — the version row stays, and any other field changed in the
same save keeps its full before-and-after. Only the PIN's two values are replaced with a
marker, leaving the fact of the change and losing the secret.

Idempotent: rows already scrubbed carry the marker instead of a PIN, and re-running
rewrites them to the same thing.
"""

import json

import frappe

# petromall is not ours. See the barakat skill.
SKIP_SITES = {"petromall.iztech.net"}

REDACTED = "***"


def _scrub_row(data: str) -> str | None:
	"""Return the rewritten JSON for one Version row, or None if it needs no change."""
	try:
		parsed = json.loads(data or "{}")
	except (ValueError, TypeError):
		return None
	if not isinstance(parsed, dict):
		return None

	changed = parsed.get("changed")
	if not isinstance(changed, list):
		return None

	touched = False
	for entry in changed:
		# Each entry is [fieldname, old, new].
		if not isinstance(entry, list) or len(entry) < 3:
			continue
		if entry[0] != "custom_pos_pin":
			continue
		if entry[1] == REDACTED and entry[2] == REDACTED:
			continue
		entry[1] = REDACTED
		entry[2] = REDACTED
		touched = True

	return json.dumps(parsed) if touched else None


def execute():
	if frappe.local.site in SKIP_SITES:
		return
	if not frappe.db.table_exists("Version"):
		return

	# get_all, not get_list: this must see every row regardless of who runs migrate.
	# A LIKE on the JSON blob is crude, but it is the only index-free way to avoid
	# loading the entire version history of the site.
	rows = frappe.get_all(
		"Version",
		filters={"ref_doctype": "Employee", "data": ["like", "%custom_pos_pin%"]},
		fields=["name", "data"],
	)

	scrubbed = 0
	for row in rows:
		rewritten = _scrub_row(row.get("data"))
		if rewritten is None:
			continue
		frappe.db.set_value(
			"Version", row["name"], "data", rewritten, update_modified=False
		)
		scrubbed += 1

	if scrubbed:
		frappe.db.commit()

	print(f"[barakat] scrubbed POS PINs from {scrubbed} Version row(s)")
