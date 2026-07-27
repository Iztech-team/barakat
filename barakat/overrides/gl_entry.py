"""Row-level scoping for the `Barakat Supplier Ledger Reader` role.

The role exists so the Inventory Keeper persona can open the admin panel's
supplier statement (`reports.suppliers`), which is a GL Entry query. A DocPerm is
per-doctype, so the read grant alone would also expose sales, payroll and journal
lines to a stock keeper. These two hooks narrow the role's holders to supplier
rows — the list query through the SQL condition, a single document through the
controller-permission hook.

Anyone who reads GL Entry through a native accounting role is left completely
alone; see `barakat.permissions.gl_entry_scope_for`.
"""

import frappe

from barakat.permissions import gl_entry_scope_for


def get_permission_query_conditions(user=None, doctype=None):
	"""SQL appended to every GL Entry list query (frappe hook).

	Returns "" for callers we do not narrow — an empty condition is dropped by
	`DatabaseQuery.get_permission_query_conditions`, so unaffected users keep the
	exact query they had before this hook existed.
	"""
	user = user or frappe.session.user
	if gl_entry_scope_for(frappe.get_roles(user)) != "supplier":
		return ""
	return "`tabGL Entry`.`party_type` = 'Supplier'"


def has_permission(doc, ptype="read", user=None, **kwargs):
	"""Controller permission for a single GL Entry (frappe hook).

	Controller hooks can only DENY, never grant, so returning True here leaves the
	DocPerm decision untouched. Without it the row filter would cover lists but not
	a direct read of one non-supplier entry.
	"""
	user = user or frappe.session.user
	if gl_entry_scope_for(frappe.get_roles(user)) != "supplier":
		return True
	party_type = doc.get("party_type") if isinstance(doc, dict) else getattr(doc, "party_type", None)
	return party_type == "Supplier"
