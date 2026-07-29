"""Read-only permission audit. Run via `bench --site <site> console < perm_audit.py`.

Captures the effective (doctype -> permissions) map each persona's bundle grants on
THIS site. Two uses:
  - baseline: run before any bundle change and keep the JSON
  - diff: run after, and compare — every removal must be justified

Never writes. Safe on production.
"""

import json

import frappe

PERM_NAMES = ("read", "write", "create", "delete", "submit", "cancel", "select", "report", "export")


def _perm_table(doctype):
	"""Custom DocPerm shadows DocPerm entirely once any row exists for a doctype."""
	if frappe.db.count("Custom DocPerm", {"parent": doctype}):
		return "Custom DocPerm"
	return "DocPerm"


def effective_perms(persona):
	"""doctype -> set of permission names granted to `persona` by its bundle here.

	Child tables (istable=1) are excluded: they inherit the parent's permissions and
	would otherwise read as "nobody has access" — a false positive.
	"""
	from barakat.overrides.staff_roles import persona_role_bundle

	roles = set(persona_role_bundle(persona))
	if not roles:
		return {}

	out = {}
	for doctype in frappe.get_all("DocType", filters={"istable": 0}, pluck="name"):
		rows = frappe.get_all(
			_perm_table(doctype),
			filters={"parent": doctype, "permlevel": 0},
			fields=["role", *PERM_NAMES],
		)
		granted = set()
		for row in rows:
			if row.role not in roles:
				continue
			granted.update(p for p in PERM_NAMES if row.get(p))
		if granted:
			out[doctype] = granted
	return out


def snapshot():
	from barakat.permissions import PERSONAS

	return {
		"site": frappe.local.site,
		"personas": {
			persona: {dt: sorted(perms) for dt, perms in sorted(effective_perms(persona).items())}
			for persona in sorted(PERSONAS)
		},
	}


# Guarded: `effective_perms` is imported by test_persona_matches_matrix, and an
# unguarded snapshot at module level would run the whole audit on every import.
# `bench console < perm_audit.py` executes with __name__ == "__main__", so piping the
# file in still prints.
if __name__ == "__main__":
	print("PERM_SNAPSHOT_JSON_START")
	print(json.dumps(snapshot(), indent=2, sort_keys=True))
	print("PERM_SNAPSHOT_JSON_END")
