"""Make the company marker mandatory on Contact, Item Price and Product Bundle.

The second half of the 2026-08-05 leak, re-measured by ticket 0001-606. The markers
added then made those three doctypes *scopable*; they did not make every ROW scoped. A
blank marker is readable by every shop — Frappe emits `ifnull(field,'')='' or field in
(...)` — so an unstamped row reads exactly like the original leak to anyone testing it.

## What this does

1. Re-runs `scope_contact_item_price_company` (documented idempotent) so anything that
   has become derivable since it last ran is filled now.
2. Updates the three Custom Fields to `reqd`, which is what closes the case for good:
   the stamping hooks run in `validate` and the mandatory check in `_validate()`
   straight after, so a save either gets stamped or is refused. Same pattern the
   presence doctypes have carried since they were built — see `presence/test_doctypes`.
3. Reports what is still blank, per doctype, with examples.

## What it deliberately does not do

It does not blank, guess or delete anything, and it does not touch `Supplier Group` or
`Territory` — every row of those on every production site is a shared ERPNext seed, and
ERPNext's installer creates them as Administrator, so making them mandatory would fail a
fresh install. See `company_marker.stamp_new_owned_master`.

Rows this cannot fill stay in the table. They stop being a leak anyway: on a site with
more than one company `company_scope.blank_marker_block` refuses a blank row of these
three doctypes for every tenant-scoped caller. On a single-company site they stay
visible, because there is nobody for them to leak to.

Idempotent: a second run finds the fields already `reqd` and the same rows still blank.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from barakat.overrides.company_marker import (
	COMPANY_MARKER_FIELDS,
	MANDATORY_MARKER_DOCTYPES,
)


def _blank_rows(doctype):
	return frappe.db.sql(
		f"""SELECT name FROM `tab{doctype}`
		     WHERE COALESCE(custom_company, '') = '' LIMIT 5""",
		pluck=True,
	)


def _blank_count(doctype):
	return frappe.db.sql(
		f"""SELECT COUNT(*) FROM `tab{doctype}` WHERE COALESCE(custom_company, '') = ''"""
	)[0][0]


def execute():
	from barakat.patches import scope_contact_item_price_company

	# Fill first, then tighten - so the report below describes the final state.
	scope_contact_item_price_company.execute()

	mandatory = {
		doctype: COMPANY_MARKER_FIELDS[doctype] for doctype in MANDATORY_MARKER_DOCTYPES
	}
	create_custom_fields(mandatory, ignore_validate=True, update=True)

	for doctype in MANDATORY_MARKER_DOCTYPES:
		field = frappe.get_meta(doctype).get_field("custom_company")
		if not field or not field.reqd:
			# Loud, but not fatal: the field is created by the patch above, so this
			# means the update was rejected rather than skipped. `sync_fixtures` runs
			# after every patch and ships the same definition, so it self-heals.
			print(
				f"[barakat] WARNING {doctype}.custom_company is still not mandatory; "
				f"sync_fixtures should repair it on this same migrate."
			)

	companies = frappe.get_all("Company", pluck="name")
	for doctype in MANDATORY_MARKER_DOCTYPES:
		remaining = _blank_count(doctype)
		if not remaining:
			print(f"[barakat] {doctype}.custom_company: mandatory, no blank rows left")
			continue
		examples = ", ".join(_blank_rows(doctype))
		hidden = "hidden from every shop" if len(companies) > 1 else "visible - one company on this site, so nothing to leak to"
		print(
			f"[barakat] {doctype}.custom_company: mandatory now, but {remaining} "
			f"existing rows are still blank and are {hidden}. Examples: {examples}"
		)
