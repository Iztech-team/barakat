"""Row-level scoping for the `Barakat Self Service` role.

The role grants read on Employee and Salary Slip so every persona keeps its own
profile and payslips. A DocPerm is per-doctype, so that read alone would expose the
whole staff directory and every salary — which is exactly the production finding this
replaces (a Cashier on niveen1 read `net_pay: 2884.62` and all 138 employee records).

These hooks narrow it to the caller's own rows: the list query through the SQL
condition, a single document through the controller-permission hook.

**Both are required.** Shipping only the query condition leaves a direct document read
open — that is precisely the difference between the Account finding (list-only, and a
false alarm) and the Salary Slip finding (full document, and real).

Callers holding an unscoped role for that doctype — HR, Manager, owners — are left
completely alone; see `barakat.permissions.self_scope_applies`. A hook registered in
`permission_query_conditions` applies to EVERY user of the doctype, so standing down
for them is not an optimisation, it is what keeps payroll working.
"""

import frappe

from barakat.permissions import self_scope_applies


def _own_employee_names(user):
	"""Employee docnames linked to this user. Empty tuple when they have none."""
	return tuple(frappe.get_all("Employee", filters={"user_id": user}, pluck="name"))


def _in_clause(names):
	"""SQL IN list.

	Returns `('')` when the caller has no Employee record, so the condition matches
	nothing. Emitting an empty `IN ()` would be a syntax error, and omitting the
	condition entirely would silently unfilter the query — the failure mode this whole
	module exists to prevent.
	"""
	if not names:
		return "('')"
	return "(" + ", ".join(frappe.db.escape(n) for n in names) + ")"


def employee_query_conditions(user=None, doctype=None):
	"""SQL appended to every Employee list query (frappe hook)."""
	user = user or frappe.session.user
	if not self_scope_applies("Employee", frappe.get_roles(user)):
		return ""
	return f"`tabEmployee`.`name` in {_in_clause(_own_employee_names(user))}"


def salary_slip_query_conditions(user=None, doctype=None):
	"""SQL appended to every Salary Slip list query (frappe hook)."""
	user = user or frappe.session.user
	if not self_scope_applies("Salary Slip", frappe.get_roles(user)):
		return ""
	return f"`tabSalary Slip`.`employee` in {_in_clause(_own_employee_names(user))}"


def _field(doc, name):
	return doc.get(name) if isinstance(doc, dict) else getattr(doc, name, None)


def employee_has_permission(doc, ptype="read", user=None, **kwargs):
	"""Controller permission for a single Employee (frappe hook).

	Controller hooks can only DENY, never grant, so returning True leaves the DocPerm
	decision untouched for everyone we do not narrow.
	"""
	user = user or frappe.session.user
	if not self_scope_applies("Employee", frappe.get_roles(user)):
		return True
	return _field(doc, "name") in _own_employee_names(user)


def salary_slip_has_permission(doc, ptype="read", user=None, **kwargs):
	"""Controller permission for a single Salary Slip (frappe hook)."""
	user = user or frappe.session.user
	if not self_scope_applies("Salary Slip", frappe.get_roles(user)):
		return True
	return _field(doc, "employee") in _own_employee_names(user)
