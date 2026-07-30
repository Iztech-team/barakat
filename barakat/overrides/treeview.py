"""Company scoping for every desk TREE view.

Frappe's tree endpoint applies no permissions at all. `frappe.desk.treeview._get_children`
builds the query directly:

    qb = (frappe.qb.from_(doctype)
            .select(name, title, is_group)
            .where(IfNull(parent_field, "") == parent)
            .where(docstatus < 2))
    return qb.orderby("name").run(as_dict=True)

No `has_permission`, no `build_match_conditions`, and the `ignore_permissions`
parameter it accepts is never read. So the tree returns every row in the table while
the list view of the same doctype filters correctly.

Measured on bom.iztech.net (prod, 2026-07-30) as `cashierbom2@gmail.com`, a Cashier of
BOM2 on a site hosting EIGHT companies:

    get_list("Item Group")  -> 7 rows, all company-less              (correct)
    tree get_children       -> 12 rows, including
                                 "Audit Group - BOM4"   <- another shop
                                 "Bakery - BOM", "Drinks - BOM",
                                 "Snacks - BOM", "Products - BOM",
                                 "osama%%% - BOM"

This is not confined to Item Group. Every tree doctype goes through the same
function — Customer Group, Supplier Group, Territory, Department, Account, Cost
Center — so the hole is one function wide and the fix belongs there, not in a
per-doctype hook.

## What this filters, and what it deliberately does not

A row survives when its Company link is EMPTY, or holds a company the caller is
permitted. Blank stays visible on purpose:

  * it is what the list view already does. Frappe's own match conditions emit
    `company IN (allowed) OR company IS NULL` unless `apply_strict_user_permissions`
    is on (it is off on every Barakat site), so hiding blanks here would make the tree
    STRICTER than the list and the two would disagree again — in the other direction.
  * `All Item Groups` is company-less, and it is both the tree's root and the "no
    group" sentinel the POS and the admin panel write products against. Hiding it
    empties the tree and breaks item creation.

A caller with no Company user permission is unrestricted (the Administrator, the shop
owner) and the rows come back untouched — this narrows nobody's access, it only
applies the restriction the site already declared.

`get_all_nodes` resolves its tree method through `frappe.override_whitelisted_method`,
so overriding `get_children` covers the whole-tree load as well as each expand.
"""

import frappe
from frappe.desk.treeview import get_children as _core_get_children


def _company_field(doctype):
	"""The doctype's Link-to-Company fieldname, or None.

	`company` on Account and Cost Center; `custom_company` on the masters Barakat
	scopes itself (Item Group, Customer Group, ...). Resolved from the meta rather
	than hard-coded so a doctype gaining the field is covered without a code change.
	"""
	for field in frappe.get_meta(doctype).fields:
		if field.fieldtype == "Link" and field.options == "Company":
			return field.fieldname
	return None


def _allowed_companies(doctype, user):
	"""Companies this caller may see, or None when they are unrestricted.

	`applicable_for` narrows a user permission to ONE doctype; an empty value applies
	to all of them. Ignoring that would let a permission meant for Sales Invoice
	silently filter the Item Group tree.
	"""
	from frappe.permissions import get_user_permissions

	entries = get_user_permissions(user).get("Company") or []
	allowed = {
		entry.get("doc")
		for entry in entries
		if not entry.get("applicable_for") or entry.get("applicable_for") == doctype
	}
	return allowed or None


def visible_rows(rows, owner_by_name, allowed):
	"""The tree rows this caller may see. Pure, so it is tested without a bench.

	`allowed` of None means unrestricted and returns everything. A row whose company
	is missing or empty is shared and kept — see the module docstring for why hiding
	blanks would break the tree root and disagree with the list view.
	"""
	if allowed is None:
		return rows
	return [row for row in rows if not owner_by_name.get(row.get("value")) or owner_by_name.get(row.get("value")) in allowed]


@frappe.whitelist()
def get_children(doctype, parent="", include_disabled=False, **filters):
	"""`frappe.desk.treeview.get_children`, with the caller's company applied."""
	rows = _core_get_children(doctype, parent, include_disabled=include_disabled, **filters)
	if not rows:
		return rows

	allowed = _allowed_companies(doctype, frappe.session.user)
	if allowed is None:
		return rows

	field = _company_field(doctype)
	if not field:
		return rows

	names = [row.get("value") for row in rows if row.get("value")]
	if not names:
		return rows

	owner_by_name = dict(
		frappe.get_all(
			doctype,
			filters={"name": ("in", names)},
			fields=["name", field],
			as_list=True,
			ignore_permissions=True,
		)
	)
	return visible_rows(rows, owner_by_name, allowed)
