"""Backfill the company marker on Contact, Item Price and Product Bundle.

The marker only scopes a row once it is FILLED: Frappe emits
`ifnull(field,'')='' or field in (...)` unless `apply_strict_user_permissions` is on, so
a blank marker is visible to every shop. Adding the field without this patch would move
the leak rather than close it.

Every value is inherited, never guessed — see `barakat.overrides.company_marker` for the
derivation order and for the measurement that motivated it. Rows that cannot be derived
are left alone and COUNTED, so the rollout has evidence instead of a silent gap; on a
single-company site the answer is unambiguous, so those are filled.

Runs before `sync_fixtures`, so it creates the custom fields itself rather than assuming
the columns already exist.

Idempotent: every statement only touches rows whose marker is still blank.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from barakat.overrides.company_marker import COMPANY_MARKER_FIELDS

MARKED_DOCTYPES = ("Contact", "Item Price", "Product Bundle")


def _blank_count(doctype):
	return frappe.db.sql(
		f"""SELECT COUNT(*) FROM `tab{doctype}` WHERE COALESCE(custom_company, '') = ''"""
	)[0][0]


def _inherit_from_item(doctype, item_field):
	frappe.db.sql(
		f"""
		UPDATE `tab{doctype}` target
		  JOIN `tabItem` item ON item.name = target.`{item_field}`
		   SET target.custom_company = item.custom_company
		 WHERE COALESCE(target.custom_company, '') = ''
		   AND COALESCE(item.custom_company, '') <> ''
		"""
	)


def _inherit_contact_from_party(link_doctype):
	frappe.db.sql(
		f"""
		UPDATE `tabContact` contact
		  JOIN `tabDynamic Link` link
		    ON link.parent = contact.name
		   AND link.parenttype = 'Contact'
		   AND link.link_doctype = %s
		  JOIN `tab{link_doctype}` party ON party.name = link.link_name
		   SET contact.custom_company = party.custom_company
		 WHERE COALESCE(contact.custom_company, '') = ''
		   AND COALESCE(party.custom_company, '') <> ''
		""",
		link_doctype,
	)


def _inherit_contact_from_user():
	"""Staff contacts carry no party link; their company is the Employee's.

	Only when that login has exactly one active Employee — a person who works in two
	shops has no single right answer and is left for a human.
	"""
	rows = frappe.db.sql(
		"""SELECT name, `user` FROM `tabContact`
		    WHERE COALESCE(custom_company, '') = '' AND COALESCE(`user`, '') <> ''""",
		as_dict=True,
	)
	filled = 0
	for row in rows:
		companies = {
			(company or "").strip()
			for company in frappe.get_all(
				"Employee",
				filters={"user_id": row.user, "status": "Active"},
				pluck="company",
				ignore_permissions=True,
			)
		}
		companies.discard("")
		if len(companies) == 1:
			frappe.db.set_value(
				"Contact", row.name, "custom_company", next(iter(companies)), update_modified=False
			)
			filled += 1
	return filled


def execute():
	create_custom_fields(COMPANY_MARKER_FIELDS, ignore_validate=True)

	before = {doctype: _blank_count(doctype) for doctype in MARKED_DOCTYPES}

	_inherit_from_item("Item Price", "item_code")
	_inherit_from_item("Product Bundle", "new_item_code")
	_inherit_contact_from_party("Customer")
	_inherit_contact_from_party("Supplier")
	from_user = _inherit_contact_from_user()

	companies = frappe.get_all("Company", pluck="name")
	sole_company = companies[0] if len(companies) == 1 else None
	if sole_company:
		for doctype in MARKED_DOCTYPES:
			frappe.db.sql(
				f"""UPDATE `tab{doctype}` SET custom_company = %s
				     WHERE COALESCE(custom_company, '') = ''""",
				sole_company,
			)

	for doctype in MARKED_DOCTYPES:
		remaining = _blank_count(doctype)
		print(
			f"[barakat] {doctype}.custom_company: filled={before[doctype] - remaining} "
			f"still_blank={remaining}"
		)
	if from_user:
		print(f"[barakat] Contact.custom_company: {from_user} taken from the linked Employee")
	if not sole_company:
		print(
			f"[barakat] {len(companies)} companies on this site - rows with no derivable "
			f"owner are left blank and reported, not guessed. A blank marker is still "
			f"readable by every shop; fill them by hand."
		)
