"""Rename a brand-new company's five ROOT accounts into the owner's language.

## Why this exists

When a shop is created, ERPNext builds the standard chart and names every
account in English. The proxy renames them into Arabic or Hebrew over REST so
the owner can read their own books — except the five roots, which ERPNext
refuses to touch:

    Account.validate() -> validate_root_details() -> throw("Root cannot be
    edited.", RootNotEditable)

That guard fires for *any* edit to an account with no parent, and
`frappe.rename_doc` is refused too ("Account not allowed to be renamed"). So
over the REST API there is no way to rename a root, and the owner is left with
"Application of Funds (Assets)", "Expenses", "Income", "Source of Funds
(Liabilities)" and "Equity" in English sitting on top of an Arabic chart —
the five most prominent lines on the screen.

## Why `frappe.db.set_value` is the right tool here, and is safe

`barakat/setup/install.py` carries a tombstone warning about writing Account
fields with `db.set_value` instead of going through the ORM. That warning is
about `root_type` / `report_type`: fields `Account.validate()` DERIVES from the
parent, so forcing them behind validation's back left the books wrong (see
`patches/repair_stock_adjustment_accounts.py`).

`account_name` is not such a field. Nothing derives it, and nothing derives
from it:

  * The docname is NOT recomputed — an Account keeps the name it was inserted
    with. Every Link field that points at these roots keeps pointing at them,
    so no link can break.
  * `root_type`, `report_type`, `lft`, `rgt` and `is_group` are untouched.
  * No runtime ERPNext code looks up an account by its root name. The English
    strings appear only in test files and in the chart TEMPLATES used at
    company creation, which have already run by the time this is called.

Verified on the test server 2026-07-28 against a company with a posted Journal
Entry: the Balance Sheet and Trial Balance roll up correctly under the renamed
Arabic roots, the desk tree expands them, and child accounts can still be
created underneath.

## The guard that keeps this safe

This refuses any company that has a single GL Entry. Renaming accounts a shop
has already traded on would rewrite the names shown on invoices and reports
that are already in the books. A company with no GL entries has nothing to
misrepresent, which is exactly the moment the proxy calls this — seconds after
creating it.

Do NOT widen this into a general account renamer. Non-root accounts rename
perfectly well through the normal REST path; the narrowness is the point.
"""

import frappe
from frappe import _

from barakat.api._root_name_validation import clean_names

# Who may do this. Mirrors api/settings.py: the Manager persona's marker role,
# plus System Manager for the tenant owner and the setup path.
ROOT_RENAMER_ROLES = frozenset({"Barakat Settings Manager", "System Manager"})


def _assert_may_rename_roots():
	if not ROOT_RENAMER_ROLES.intersection(frappe.get_roles(frappe.session.user)):
		frappe.throw(
			_("You are not permitted to rename the chart of accounts."),
			frappe.PermissionError,
		)


@frappe.whitelist()
def set_root_account_names(company, names):
	"""Rename the root accounts of a company that has not traded yet.

	`names` is a JSON object mapping each root's CURRENT account_name to its new
	one, e.g. `{"Expenses": "المصاريف"}`. Roots not mentioned are left alone, and
	a mapping entry that matches no root is ignored — so this is idempotent and
	safe to call again.

	Returns `{"renamed": <count>, "roots": {docname: new_name}}`.
	"""
	_assert_may_rename_roots()

	try:
		mapping = clean_names(names)
	except ValueError as exc:
		frappe.throw(_(str(exc)), frappe.ValidationError)

	if not frappe.db.exists("Company", company):
		frappe.throw(_("Company {0} not found").format(company), frappe.DoesNotExistError)

	# The safety gate. See the module docstring.
	if frappe.db.count("GL Entry", {"company": company}):
		frappe.throw(
			_("This shop already has accounting entries, so its chart of accounts cannot be renamed."),
			frappe.ValidationError,
		)

	roots = frappe.get_all(
		"Account",
		filters={"company": company, "parent_account": ["in", ["", None]]},
		fields=["name", "account_name"],
	)

	# Names already used in this company, so a rename cannot create a duplicate
	# that ERPNext's own validation would have caught on a normal save.
	taken = {
		r.account_name
		for r in frappe.get_all(
			"Account", filters={"company": company}, fields=["name", "account_name"]
		)
		if r.name not in {root.name for root in roots}
	}

	applied = {}
	for root in roots:
		new_name = mapping.get(root.account_name)
		if not new_name or new_name == root.account_name:
			continue
		if new_name in taken:
			frappe.log_error(
				f"Root rename skipped for {root.name}: {new_name!r} is already used in {company}",
				"barakat.chart_of_accounts",
			)
			continue
		# Written straight to the column: ERPNext refuses to save a root through
		# the ORM at all, and this field has no validation or side effects to
		# skip. See the module docstring for why that is safe.
		frappe.db.set_value("Account", root.name, "account_name", new_name)
		applied[root.name] = new_name

	if applied:
		frappe.db.commit()
		frappe.clear_cache(doctype="Account")

	return {"renamed": len(applied), "roots": applied}
