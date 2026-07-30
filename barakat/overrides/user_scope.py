"""Row-level scoping for the `User` doctype: yourself, or your own shop's staff.

`read` on `User` is not granted by any Barakat role — it is Frappe's own baseline for
a desk session, which every System User has because that is what makes the desk work
at all. So no part of the persona matrix ever covered it, and the matrix guards could
not see it either: they iterate `MODULE_DOCTYPES`, and a doctype in NO module is
invisible to them. That is the same blind spot that hid `Cost Center`.

Measured on bom.iztech.net (prod, 2026-07-30). This is a cross-SHOP leak, not merely
a directory listing:

  * a Cashier saw 26 of the site's 28 users — every colleague's name and email;
  * the site hosts EIGHT companies, and `bom3manager@gmail.com`, Manager of BOM3
    (one employee), saw 21 users belonging to BOM, a different shop entirely.

Both hooks are required, for the reason `self_service.py` already records: the query
condition covers lists, the controller hook covers opening one document directly.
Shipping only the first leaves a direct read open.

## Why this is scoped by the caller's OWN Employee company

There is no company field on `User`. The link is `Employee.user_id` → `Employee.company`,
so "my shop's users" means "users whose Employee sits in a company one of MY Employee
records sits in". A user with no Employee record at all (the owner account, service
accounts) is therefore not shop staff and is not listed — except to the unrestricted
tier, and except yourself, always.

## Failure behaviour, chosen deliberately

If the caller has no Employee record, the company tier degrades to SELF rather than to
"everyone": an unknown shop must not resolve to every shop. The one thing that never
degrades is seeing YOURSELF — losing that breaks the profile page and the desk's own
"switch to me" plumbing for no security gain.
"""

import frappe

from barakat.permissions import user_scope_for


def _own_companies(user):
	"""Companies this caller's own Employee record(s) belong to."""
	return {
		c
		for c in frappe.get_all("Employee", filters={"user_id": user}, pluck="company")
		if c
	}


def _visible_user_names(user):
	"""Every User this caller may see under the `company` tier, including themselves."""
	companies = _own_companies(user)
	if not companies:
		# No Employee record => no shop. Degrade to self, never to everyone.
		return {user}

	peers = frappe.get_all(
		"Employee",
		filters={"company": ("in", sorted(companies)), "user_id": ("is", "set")},
		pluck="user_id",
	)
	names = {p.strip() for p in peers if p and p.strip()}
	names.add(user)
	return names


def _in_clause(names):
	"""SQL IN list. Never empty — `IN ()` is a syntax error, and dropping the
	condition would silently unfilter the query, which is the failure this module
	exists to prevent."""
	if not names:
		return "('')"
	return "(" + ", ".join(frappe.db.escape(n) for n in sorted(names)) + ")"


def user_query_conditions(user=None):
	"""SQL appended to every permitted `User` list query."""
	user = user or frappe.session.user
	scope = user_scope_for(frappe.get_roles(user))

	if scope == "unrestricted":
		return ""
	if scope == "self":
		return f"`tabUser`.`name` = {frappe.db.escape(user)}"
	return f"`tabUser`.`name` in {_in_clause(_visible_user_names(user))}"


def user_has_permission(doc, ptype=None, user=None):
	"""Gate opening ONE User document. Mirrors the list condition exactly.

	Returning True here does not GRANT anything — the DocPerm still has to allow the
	ptype. This only removes rows the caller must not reach.
	"""
	user = user or frappe.session.user
	scope = user_scope_for(frappe.get_roles(user))

	if scope == "unrestricted":
		return True

	name = getattr(doc, "name", doc)
	if name == user:
		return True  # always yourself
	if scope == "self":
		return False
	return name in _visible_user_names(user)
