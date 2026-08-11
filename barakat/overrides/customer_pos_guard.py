"""Refuse a till-created Customer when its POS Profile forbids it.

WHAT THIS IS AND IS NOT. The desktop POS authenticates as a Manager or Branch
Supervisor device session, with the cashier identified only by a PIN — see the
Cashier row in barakat/persona_matrix.py. So the server cannot tell a till's
`POST /api/resource/Customer` from the admin panel's by looking at the user, and
a role permission cannot express this rule at all.

What the till CAN do is say which profile it is running under, which it does by
stamping `custom_pos_profile`. This guard trusts that stamp. A client that lies
about not being a till slips through; no cashier operating the app can do that.
It is a guard against a cashier, not a security boundary, and must not be
described as one.
"""

import frappe
from frappe import _
from frappe.utils import cint


def guard_pos_customer_creation(doc, method=None):
	profile = (doc.get("custom_pos_profile") or "").strip()
	if not profile:
		# The admin panel. Not a till — nothing to enforce, and no read to pay for.
		return

	allowed = frappe.db.get_value(
		"POS Profile", profile, "custom_allow_customer_creation"
	)

	if allowed is None:
		# Fail closed. `get_value` returns None both for "no such profile" and
		# for a profile missing the field; neither is a state in which we can
		# prove the till is permitted.
		frappe.throw(
			_("POS Profile {0} was not found, so this till cannot add customers.").format(
				profile
			),
			title=_("Adding customers not allowed"),
		)

	if not cint(allowed):
		frappe.throw(
			_(
				"This till is not allowed to add customers. "
				"Enable 'Allow creating customers' on POS Profile {0}."
			).format(profile),
			title=_("Adding customers not allowed"),
		)
