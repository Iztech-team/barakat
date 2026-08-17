"""Take the till credential off everyone who is not on a till.

An employee moved from Cashier to HR kept a WORKING POS PIN: the admin panel hid the
field and nothing between it and the till ever looked at the role again. The hook on
Employee `validate` closes that from now on, but only for records that are saved
again — and nobody re-saves an HR clerk to fix a bug they cannot see.

Measured on the production bench on 2026-08-17: ten employees across two sites hold a
PIN they cannot legitimately use, one of them on a customer's live shop.

Three guards, each of which has a real site behind it:

- **petromall is not ours.** It shares a bench with production and has the barakat app
  installed, which is not permission to write to it.
- **A site may not have the column at all.** `izdehar.iztech.net` does not, today.
  Reading it would take that site's migration down.
- **Idempotent.** A second run finds nothing left to clear and writes nothing.

Writes through `frappe.db.set_value`, not `doc.save()`, deliberately: saving would
fire the persona role re-assertion for every affected employee, which is a large,
unrelated side effect for a field this patch is emptying.
"""

import frappe

from barakat.permissions import is_pos_persona

# petromall is not ours. See the barakat skill.
SKIP_SITES = {"petromall.iztech.net"}


def rows_to_clear(rows):
	"""The employee ids whose PIN must go.

	Pure, so the rule can be tested without a bench. A row is cleared when it has a
	PIN and its preset is not a till persona — blank and unrecognised presets
	included, which is the same allow-list the hook and the till apply.
	"""
	return [
		row["name"]
		for row in rows
		if (row.get("custom_pos_pin") or "").strip()
		and not is_pos_persona(row.get("custom_role_preset"))
	]


def execute():
	if frappe.local.site in SKIP_SITES:
		return

	# A site whose fixtures have not synced has neither field. Both are checked:
	# without the preset there is no way to tell a cashier from an HR clerk, and
	# guessing would either clear every PIN on the site or none of them.
	if not frappe.db.has_column("Employee", "custom_pos_pin"):
		return
	if not frappe.db.has_column("Employee", "custom_role_preset"):
		return

	# get_all, not get_list: this must see every employee regardless of who runs
	# migrate. Status is not filtered — a `Left` HR clerk's PIN is no more
	# legitimate than an active one's.
	rows = frappe.get_all(
		"Employee",
		fields=["name", "custom_pos_pin", "custom_role_preset"],
		limit_page_length=0,
	)

	names = rows_to_clear(rows)
	for name in names:
		frappe.db.set_value("Employee", name, "custom_pos_pin", "", update_modified=False)

	if names:
		frappe.db.commit()

	# The ids, never the PINs, and never the count alone: a shop has to be told who
	# is about to find their PIN stops working.
	print(
		f"[barakat] cleared the POS PIN of {len(names)} non-till employee(s)"
		+ (f": {', '.join(names)}" if names else "")
	)
