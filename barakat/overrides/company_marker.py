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

## Why the marker is also MANDATORY (2026-09-02)

Stamping and backfilling are both best-effort: the stamp fills nothing when it cannot
derive an owner, and the backfill deliberately refuses to guess on a multi-company site.
Every row they leave behind is blank, and a blank row is readable by every shop — which
is the original leak, at row granularity instead of doctype granularity.

The presence module already solved this and it is the pattern followed here: make the
marker `reqd`, so the blank case cannot be reached at all (see
`presence/test_doctypes.py`). Ordering makes it safe — Frappe runs `validate` hooks in
`run_before_save_methods()` and the mandatory check in `_validate()` immediately after,
so the stamp below always gets first refusal.

`MANDATORY_MARKER_DOCTYPES` is the three with a parent to inherit from, NOT all five.
`Supplier Group` and `Territory` are excluded on purpose: every row on every production
site is an ERPNext seed that is genuinely shared and deliberately blank, and ERPNext's
own installer creates them as Administrator. Making those mandatory would fail a fresh
install. See `stamp_new_owned_master`.

Rows that predate this still exist and are still blank, so the schema rule is paired
with a read-time one: `company_scope.blank_marker_block` refuses a blank row of these
three doctypes on a site that has more than one company. Both are needed — the schema
stops new blanks, the query condition contains the old ones.

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

# The three whose owner is always derivable from a parent record, so a blank is never
# legitimate and the field can be `reqd`. Read the module docstring before adding a
# fourth — `Supplier Group` and `Territory` are excluded deliberately, not by omission.
MANDATORY_MARKER_DOCTYPES = ("Contact", "Item Price", "Product Bundle")

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
			"reqd": 1,
		}
	],
	"Item Price": [
		{
			"fieldname": "custom_company",
			"label": "Company (Barakat)",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "price_list",
			"reqd": 1,
		}
	],
	"Product Bundle": [
		{
			"fieldname": "custom_company",
			"label": "Company (Barakat)",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "description",
			"reqd": 1,
		}
	],
	# The two tree masters. Marker only, no backfill -- see `stamp_new_owned_master`.
	"Supplier Group": [
		{
			"fieldname": "custom_company",
			"label": "Company (Barakat)",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "supplier_group_name",
		}
	],
	"Territory": [
		{
			"fieldname": "custom_company",
			"label": "Company (Barakat)",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "territory_name",
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


def sole_site_company():
	"""The site's only Company, or "" when it hosts none or several.

	The last resort in every derivation below, and not a guess: with one company on
	the site there is exactly one answer a row could possibly have. The backfill patch
	already fills blanks this way for the same reason — this puts the same rule on the
	live path, so a single-company site never has to refuse a save once the marker is
	mandatory.

	`ignore_permissions` matters: this must count the SITE's companies, and a
	tenant-scoped caller's own `get_all("Company")` returns just their own — which
	would make an 8-company site look single-company to them and hand every row the
	caller's company.
	"""
	names = frappe.get_all("Company", pluck="name", limit=2, ignore_permissions=True)
	return names[0] if len(names) == 1 else ""


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

	return _sole_permitted_company() or sole_site_company()


def stamp_contact(doc, method=None):
	if (doc.get("custom_company") or "").strip():
		return
	company = contact_company(doc)
	if company:
		doc.custom_company = company


def _owned_row_company(item_code):
	"""The company for a row that hangs off an Item, in descending trustworthiness.

	The Item first, then the caller's sole Company permission, then the site's sole
	Company — the same order `contact_company` uses, and for the same reason: each
	step is an authority the caller cannot forge, and the fallbacks only fire where
	there is exactly one possible answer.

	The two fallbacks are not decoration. ERPNext inserts an Item Price of its own
	whenever `standard_rate` is set on an Item, so once the marker is mandatory a
	derivation that returns "" does not leave a blank row — it fails the Item save.
	"""
	return (
		_company_of_item(item_code) or _sole_permitted_company() or sole_site_company()
	)


def stamp_item_price(doc, method=None):
	if (doc.get("custom_company") or "").strip():
		return
	company = _owned_row_company(doc.get("item_code"))
	if company:
		doc.custom_company = company


def stamp_product_bundle(doc, method=None):
	if (doc.get("custom_company") or "").strip():
		return
	company = _owned_row_company(doc.get("new_item_code"))
	if company:
		doc.custom_company = company


def stamp_new_owned_master(doc, method=None):
	"""Give a NEWLY created Supplier Group / Territory its creator's company.

	Wired on `before_insert`, never on `validate`, and there is deliberately no
	backfill. Both rules follow from the same fact: unlike a Contact or an Item Price,
	these have no parent record to inherit from, so the only available source is
	whoever is saving — and applying that to an existing row would be a land grab.

	Every Supplier Group and Territory on every production site today is an ERPNext
	seed (checked 2026-08-05 across all five: the 8 stock supplier groups and the 3
	stock territories, and not one shop-created row anywhere). They are genuinely
	shared: `Local` carries 801 of BOM's suppliers, `Services` is used by BOM AND
	BOM4, and `Palestinian Territory, Occupied` is used by all eight companies.
	Stamping one of those would not close a leak — the names are ERPNext's own — it
	would empty another shop's picker and push them into creating a duplicate.

	So existing rows stay blank, which keeps them visible to everyone, exactly as
	`All Item Groups` and the stock Customer Groups already are (see
	`overrides/treeview.py`). What this does buy: the first row a SHOP creates is
	scoped from birth rather than after someone notices.

	ERPNext's own seeds are inserted by Administrator, for whom
	`_sole_permitted_company` returns "" — so a fresh install still comes up shared.
	"""
	if (doc.get("custom_company") or "").strip():
		return
	company = _sole_permitted_company()
	if company:
		doc.custom_company = company
