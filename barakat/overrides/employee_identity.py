"""Let one login hold an Employee record in more than one company.

ERPNext refuses it. `Employee.validate_duplicate_user_id`
(erpnext/setup/doctype/employee/employee.py) asks only:

    WHERE user_id = <email> AND status = 'Active' AND name != <this employee>

with no company term. So adding the same person to a second shop on the same site
fails with "User X is already assigned to Employee Y" — naming a record the manager
of the second shop usually cannot even see, because the admin panel's staff list is
company-scoped.

That check is wrong for this product. An Employee here is a per-company job: it
carries the company, branches, POS PIN, salary and joining date. The User is the
site-global identity. One person working two shops is two jobs and one login, and
the rest of the system already agrees — `validate_employee_pin` scopes PIN
uniqueness per company on purpose, and `reassert_company_user_permission` is
add-only precisely because "staff may legitimately span shops".

## Why a before_validate hook and not `override_doctype_class`

hrms already claims `override_doctype_class["Employee"]` with `EmployeeMaster`, and
Frappe resolves that hook by taking the LAST entry from the installed-app order.
That order is per-site and is NOT consistent across this bench — measured
2026-08-01, `qa-test` and `osa` install barakat after hrms (so a barakat override
would win) while `fatima` and `qa-training` install hrms LAST (so it would silently
lose, and the fix would simply not apply there with no error anywhere).

`before_validate` runs ahead of the controller's own `validate()`, so shadowing the
method on the instance works whatever the app order is, and leaves hrms's
`EmployeeMaster` (which owns Employee autonaming) completely alone.
"""

import frappe
from frappe import _


def _same_company_duplicate(doc):
	"""ERPNext's duplicate-login check, scoped to this employee's own company."""
	if not doc.user_id:
		return
	duplicate = frappe.db.get_value(
		"Employee",
		{
			"user_id": doc.user_id,
			"status": "Active",
			"company": doc.company,
			"name": ("!=", doc.name or "__new__"),
		},
		"name",
	)
	if duplicate:
		frappe.throw(
			_("User {0} is already assigned to Employee {1} in {2}").format(
				doc.user_id, duplicate, doc.company
			),
			frappe.DuplicateEntryError,
		)


def scope_duplicate_login_to_company(doc, method=None):
	"""Replace ERPNext's site-wide duplicate-login check with a per-company one.

	Wired on Employee `before_validate`, which runs before the controller's
	`validate()` calls `validate_duplicate_user_id`. Assigning to the instance
	shadows the class method; `get_valid_dict` only walks meta fields, so the
	attribute never reaches the database.
	"""

	def check():
		_same_company_duplicate(doc)

	doc.validate_duplicate_user_id = check
