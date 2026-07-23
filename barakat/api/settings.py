"""Elevated writer for the two rounding fields the AP's Rounding page owns.

## Why this exists

The Rounding page writes exactly two fields:

  * `Global Defaults.disable_rounded_total`
  * `System Settings.rounding_method`

ERPNext permissions are per-DOCTYPE, never per-field. So the only way to let a
Manager save that page used to be granting `Barakat Settings Manager` **write on
System Settings and Global Defaults** — which also hands them session policy,
password policy, and every other global setting on those singles. The 2026-07-21
review flagged that (New-C): "well beyond the Rounding page it was added for".

The owner's call was: Managers need the rounding controls, not the rest. Since a
DocPerm cannot express that, the role now holds **read only** (so the page can
still display current values) and the two writes happen here, elevated, after an
explicit role check. Blast radius: two fields instead of two whole doctypes.

Do NOT widen this to a generic settings writer — the narrowness is the point.
"""

import frappe
from frappe import _
from frappe.utils import cint

# Who may change rounding. `Barakat Settings Manager` is the marker role carried by
# the Manager persona; System Manager is the tenant owner / setup path.
ROUNDING_WRITER_ROLES = frozenset({"Barakat Settings Manager", "System Manager"})


def _assert_may_write_rounding():
	if not ROUNDING_WRITER_ROLES.intersection(frappe.get_roles(frappe.session.user)):
		frappe.throw(
			_("You are not permitted to change rounding settings."),
			frappe.PermissionError,
		)


@frappe.whitelist()
def set_rounding_settings(disable_rounded_total=None, rounding_method=None):
	"""Set the rounding fields, and nothing else on those singles.

	Both arguments are optional — the AP sends only what changed. Returns the
	values as they stand afterwards so the caller can echo them back.

	Saved through the document (not `db.set_single_value`) so ERPNext's own
	validation and on_update side effects still run; only the PERMISSION check is
	bypassed, and only after the role check above.
	"""
	_assert_may_write_rounding()

	if disable_rounded_total is not None:
		gd = frappe.get_single("Global Defaults")
		gd.disable_rounded_total = 1 if cint(disable_rounded_total) else 0
		gd.flags.ignore_permissions = True
		gd.save()

	if rounding_method is not None:
		# Validate against the field's own Select options rather than a hard-coded
		# list here — the options are ERPNext's to change between versions, and a
		# stale copy would silently reject a legitimate method.
		field = frappe.get_meta("System Settings").get_field("rounding_method")
		allowed = [o.strip() for o in (field.options or "").split("\n") if o.strip()]
		if rounding_method not in allowed:
			frappe.throw(
				_("Unknown rounding method {0}. Expected one of: {1}").format(
					rounding_method, ", ".join(allowed)
				)
			)
		ss = frappe.get_single("System Settings")
		ss.rounding_method = rounding_method
		ss.flags.ignore_permissions = True
		ss.save()

	return {
		"disable_rounded_total": cint(
			frappe.db.get_single_value("Global Defaults", "disable_rounded_total")
		),
		"rounding_method": frappe.db.get_single_value("System Settings", "rounding_method"),
	}
