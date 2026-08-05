"""Stamp the owning company on doctypes ERPNext ships with no company field.

## Why this exists

The tenant boundary is ONE `Company` User Permission per staff login
(`staff_roles.reassert_company_user_permission`). Frappe applies that permission by
walking the **Link fields of the doctype being queried** and filtering any field whose
`options == "Company"`. A doctype with no such field is therefore not filtered at all —
the permission has nothing to bind to, and the query comes back unscoped.

That is not a theoretical gap. Measured on prod `bom.iztech.net` (8 companies) on
2026-08-05, as a Cashier of BOM2 — the lowest persona there is:

    Customer     2 of 2336     scoped, because Customer.custom_company exists
    Item         1 of 19202    scoped, because Item.custom_company exists
    Contact    405 of 405      UNSCOPED - 272 rows belonging to BOM, 271 with a mobile
    Item Price 34719 of 34719  UNSCOPED - every shop's selling AND supplier cost prices

Same user, same call shape, opposite result. The only difference was the marker field.
`Barakat Customers Writer` / `Barakat Products Writer` (Branch Supervisor, Manager) also
carry write+delete on those two, so it was cross-tenant modification, not just disclosure.

`company_scope.py` does not cover this: it stands down entirely for a single-company
caller, which is every caller on every site today. It narrows a person who works in two
shops; it was never the tenant boundary and cannot substitute for the marker.

## The rule

A shop-owned doctype must carry a `Company` Link field, and every row must have it
filled. A BLANK marker is visible to EVERYONE — Frappe emits
`ifnull(field,'')='' or field in (...)` unless `apply_strict_user_permissions` is on —
so stamping is half the fix and the backfill patch is the other half. Both are required.

## Where the value comes from

Never invented, always inherited from the record that already carries a trustworthy
marker, so a mis-stamp cannot file one shop's row under another:

    Item Price      -> its Item                       (item_code is mandatory)
    Product Bundle  -> its Item                       (new_item_code is mandatory)
    Contact         -> the Customer or Supplier it links to, else the Employee behind
                       its `user`, else the caller's company - and only when the caller
                       holds exactly one Company User Permission, so it is never a guess

The caller's `X-Barakat-Company` header is deliberately NOT used as a source. It is
caller-controlled; `company_scope` can accept it because declaring a shop there also
pins you to it, but a stamp is a lasting write and must come from an authority the
caller cannot forge.

Existing values are never overwritten. Staff move a record between shops by hand, and
re-deriving on every save would undo that silently.
"""

import frappe

# The markers this module owns. Kept here rather than only in
# `fixtures/custom_field.json` so the backfill patch can create them itself: on a site
# migrating up to this version the patch runs BEFORE `sync_fixtures`, so the column does
# not exist yet. `test_company_markers.py` asserts the two definitions agree.
COMPANY_MARKER_FIELDS = {
	"Contact": [
		{
			"fieldname": "custom_company",
			"label": "Company (Barakat)",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "company_name",
		}
	],
	"Item Price": [
		{
			"fieldname": "custom_company",
			"label": "Company (Barakat)",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "price_list",
		}
	],
	"Product Bundle": [
		{
			"fieldname": "custom_company",
			"label": "Company (Barakat)",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "description",
		}
	],
}


def _company_of_item(item_code):
	if not item_code:
		return ""
	return (frappe.db.get_value("Item", item_code, "custom_company") or "").strip()


def _sole_permitted_company(user=None):
	"""The caller's company, but only when there is exactly one honest answer.

	Read from their `Company` User Permissions rather than their Employee rows: the
	User Permission is what actually scopes their reads, so stamping with anything
	else could file a row where its author cannot see it.
	"""
	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return ""
	values = {
		(value or "").strip()
		for value in frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "Company"},
			pluck="for_value",
			ignore_permissions=True,
		)
	}
	values.discard("")
	return next(iter(values)) if len(values) == 1 else ""


def contact_company(doc):
	"""Derive a Contact's company. See the module docstring for the order."""
	for row in doc.get("links") or []:
		link_doctype = row.get("link_doctype")
		link_name = row.get("link_name")
		if link_doctype in ("Customer", "Supplier") and link_name:
			owner = (
				frappe.db.get_value(link_doctype, link_name, "custom_company") or ""
			).strip()
			if owner:
				return owner

	user = (doc.get("user") or "").strip()
	if user:
		rows = frappe.get_all(
			"Employee",
			filters={"user_id": user, "status": "Active"},
			pluck="company",
			ignore_permissions=True,
		)
		companies = {(company or "").strip() for company in rows}
		companies.discard("")
		if len(companies) == 1:
			return next(iter(companies))

	return _sole_permitted_company()


def stamp_contact(doc, method=None):
	if (doc.get("custom_company") or "").strip():
		return
	company = contact_company(doc)
	if company:
		doc.custom_company = company


def stamp_item_price(doc, method=None):
	if (doc.get("custom_company") or "").strip():
		return
	company = _company_of_item(doc.get("item_code"))
	if company:
		doc.custom_company = company


def stamp_product_bundle(doc, method=None):
	if (doc.get("custom_company") or "").strip():
		return
	company = _company_of_item(doc.get("new_item_code"))
	if company:
		doc.custom_company = company
