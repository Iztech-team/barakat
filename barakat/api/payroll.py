"""Elevated poster for the two Journal Entries payroll produces.

## Why this exists

Running payroll writes to the general ledger twice:

  * an **accrual** when a salary slip is submitted — what the wage COST, debited to
    the shop's wages expense account and credited to payroll payable;
  * a **payment** when the slip is paid — payroll payable debited, cash/bank
    credited.

The proxy used to create and submit both under the CALLER's own ERPNext session. So
running payroll required `Journal Entry` create+submit, which in the matrix belongs
to `finance` — and HR is `finance: none`. The result: HR holds `salary: write`, the
admin panel shows a Generate Slip button, and every one of the four salary write
endpoints answered 403 from ERPNext. HR could not run payroll at all.

The two ways out of that were: hand HR `Journal Entry` write (which is the whole
payables and journal surface, reachable from /app — far past payroll), or post these
two specific vouchers elevated. This is the second. Same shape as
`barakat.api.settings.set_rounding_settings`: an explicit role check, then
`ignore_permissions`, and nothing else widened.

## What stops this being "post any journal you like"

`ignore_permissions` is only as narrow as its validation, so the guard below is the
whole point of the module:

  * the caller must hold a payroll role (`POSTER_ROLES`);
  * `slip` must be a real, submitted Salary Slip belonging to `company`;
  * the voucher must be recognisably one of ours — the external id has to be the
    accrual id for that slip, or carry the slip's own payment prefix;
  * exactly two lines, balanced, both accounts in the same company.

A caller who satisfies all of that has described a payroll voucher for a payroll
document they were already allowed to create. There is no path here to an arbitrary
journal, to another company's accounts, or to an unbalanced entry.
"""

import json

import frappe
from frappe import _

# Who may post payroll to the books. `Barakat Salary Writer` is the generated role
# behind `salary: write` (Manager and HR); System Manager is the owner / setup path.
POSTER_ROLES = frozenset({"Barakat Salary Writer", "System Manager"})

# Mirrors proxy-barakat src/modules/hr/service.ts — keep the two in step.
PAY_ID_SEP = "::"
ACCRUAL_PREFIX = "ACCRUAL"


def _assert_may_post():
	if not POSTER_ROLES.intersection(frappe.get_roles(frappe.session.user)):
		frappe.throw(_("You are not permitted to post payroll entries."), frappe.PermissionError)


def _assert_may_post_for(company):
	"""The caller must actually work at `company`.

	Roles are site-global — that is the whole premise `overrides/company_scope.py`
	exists to work around — so holding `Barakat Salary Writer` says a person may post
	payroll SOMEWHERE, never where. Without this, an Accountant of shop A could post a
	journal into shop B's ledger; every other guard below checks the payload against
	`company`, and none of them checks `company` against the caller.

	System Manager is exempt: it is the owner / setup path, and it is never narrowed
	anywhere else in the app either.
	"""
	if "System Manager" in frappe.get_roles(frappe.session.user):
		return
	mine = frappe.get_all(
		"Employee",
		filters={"user_id": frappe.session.user, "status": "Active", "company": company},
		limit=1,
		ignore_permissions=True,
	)
	if not mine:
		frappe.throw(
			_("You cannot post payroll for another company."), frappe.PermissionError
		)


def _assert_is_payroll_voucher(external_id, slip):
	"""The voucher must belong to THIS slip, as an accrual or as a payment.

	Accrual: `ACCRUAL::<slip>` — one per slip, which is also how the proxy dedupes.
	Payment: `<slip>::<nonce>` — many per slip, since a slip may be paid in parts.
	"""
	if external_id == f"{ACCRUAL_PREFIX}{PAY_ID_SEP}{slip}":
		return
	if external_id.startswith(f"{slip}{PAY_ID_SEP}"):
		return
	frappe.throw(
		_("This is not a payroll voucher for {0}.").format(slip),
		frappe.PermissionError,
	)


def _assert_balanced_two_liner(accounts, company):
	if len(accounts) != 2:
		frappe.throw(_("A payroll entry must have exactly two lines."), frappe.ValidationError)

	debit = sum(float(a.get("debit_in_account_currency") or 0) for a in accounts)
	credit = sum(float(a.get("credit_in_account_currency") or 0) for a in accounts)
	if round(debit, 2) != round(credit, 2):
		frappe.throw(_("A payroll entry must balance."), frappe.ValidationError)
	if round(debit, 2) <= 0:
		frappe.throw(_("A payroll entry must be for a positive amount."), frappe.ValidationError)

	for row in accounts:
		name = row.get("account")
		owner = frappe.db.get_value("Account", name, "company")
		if not owner:
			frappe.throw(_("Account {0} does not exist.").format(name), frappe.ValidationError)
		if owner != company:
			frappe.throw(
				_("Account {0} belongs to another company.").format(name),
				frappe.PermissionError,
			)


@frappe.whitelist()
def post_payroll_journal(company, slip, external_id, remark=None, posting_date=None, accounts=None):
	"""Create and submit one payroll Journal Entry, elevated. Returns its name.

	`accounts` is the two-line payload the proxy already builds (it resolves which
	accounts a shop uses); everything about it is re-checked here rather than
	trusted, because this runs with permissions off.

	Idempotent on `external_id`: the accrual is posted once per slip and the proxy
	retries on failure, so a duplicate call must return the existing voucher rather
	than double-charge the books.
	"""
	_assert_may_post()
	_assert_may_post_for(company)

	if isinstance(accounts, str):
		accounts = json.loads(accounts)
	if not accounts:
		frappe.throw(_("A payroll entry needs its account lines."), frappe.ValidationError)

	slip_row = frappe.db.get_value(
		"Salary Slip", slip, ["name", "company", "docstatus"], as_dict=True
	)
	if not slip_row:
		frappe.throw(_("Salary slip {0} not found.").format(slip), frappe.DoesNotExistError)
	if slip_row.company != company:
		frappe.throw(_("That salary slip belongs to another company."), frappe.PermissionError)
	if slip_row.docstatus != 1:
		frappe.throw(
			_("Only a submitted salary slip can be posted to the books."),
			frappe.ValidationError,
		)

	_assert_is_payroll_voucher(external_id, slip)
	_assert_balanced_two_liner(accounts, company)

	existing = frappe.db.get_value("Journal Entry", {"custom_external_id": external_id}, "name")
	if existing:
		return existing

	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": company,
			"posting_date": posting_date or frappe.utils.today(),
			"custom_external_id": external_id,
			"user_remark": remark or f"Payroll {slip}",
			"accounts": accounts,
		}
	)
	je.flags.ignore_permissions = True
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name
