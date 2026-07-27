import re

import frappe
from frappe import _

from barakat.loyalty_tier_names import first_duplicate_tier_name


def validate_item_disable(doc, method):
	if not doc.disabled:
		return

	was_disabled_before = frappe.db.get_value("Item", doc.name, "disabled")
	if was_disabled_before:
		return

	item_company = doc.custom_company
	if not item_company:
		return

	open_shifts = frappe.db.sql(
		"""
		SELECT name, pos_profile, company
		FROM `tabPOS Opening Entry`
		WHERE status = 'Open'
		  AND company = %s
		LIMIT 5
		""",
		(item_company,),
		as_dict=True,
	)

	if not open_shifts:
		return

	shift_lines = "".join(
		f"<li><b>{s['name']}</b> — {s['pos_profile']} ({s['company']})</li>"
		for s in open_shifts
	)
	frappe.throw(
		title=_("Cannot Disable Item"),
		msg=_(
			"You cannot disable this item while there are open POS shifts for company <b>{0}</b>. "
			"Please close all open POS Opening Entries first:<ul>{1}</ul>"
		).format(item_company, shift_lines),
	)


def open_shifts_for_company(company):
	"""Open POS Opening Entries for a company, or across all companies when
	`company` is falsy — a Pricing Rule with no company applies site-wide, so
	any open shift can still be honouring it."""
	if company:
		return frappe.db.sql(
			"""
			SELECT name, pos_profile, company
			FROM `tabPOS Opening Entry`
			WHERE status = 'Open'
			  AND company = %s
			LIMIT 5
			""",
			(company,),
			as_dict=True,
		)
	return frappe.db.sql(
		"""
		SELECT name, pos_profile, company
		FROM `tabPOS Opening Entry`
		WHERE status = 'Open'
		LIMIT 5
		""",
		as_dict=True,
	)


def _shift_lines(open_shifts):
	return "".join(
		f"<li><b>{s['name']}</b> — {s['pos_profile']} ({s['company']})</li>"
		for s in open_shifts
	)


def guard_pricing_rule_delete(doc, method):
	"""A till applies promotions from its own mirror, refreshed on sync. Deleting
	a rule mid-shift leaves tills granting a promotion that no longer exists, and
	the invoices they push reference it by name."""
	open_shifts = open_shifts_for_company(doc.company)
	if not open_shifts:
		return

	frappe.throw(
		title=_("Cannot Delete Promotion"),
		msg=_(
			"You cannot delete this promotion while there are open POS shifts. "
			"Please close all open POS Opening Entries first:<ul>{0}</ul>"
		).format(_shift_lines(open_shifts)),
	)


def validate_pricing_rule_disable(doc, method):
	"""Same reasoning as the delete guard: tills honour `disable` only as of
	their last sync, so switching a rule off mid-shift still lets it be granted."""
	if not doc.disable:
		return

	# `validate` also runs on insert, and Frappe assigns `doc.name` before
	# run_before_save_methods() — so a brand-new document already has a name and
	# frappe.db.get_value below would return None, indistinguishable from "was
	# enabled before". Creating an already-disabled rule during an open shift is
	# harmless (no till has ever seen it, so none can grant it), so let it save.
	if doc.is_new():
		return

	was_disabled_before = frappe.db.get_value("Pricing Rule", doc.name, "disable")
	if was_disabled_before:
		return

	open_shifts = open_shifts_for_company(doc.company)
	if not open_shifts:
		return

	frappe.throw(
		title=_("Cannot Disable Promotion"),
		msg=_(
			"You cannot disable this promotion while there are open POS shifts. "
			"Please close all open POS Opening Entries first:<ul>{0}</ul>"
		).format(_shift_lines(open_shifts)),
	)


def validate_customer_mobile_unique(doc, method):
	mobile = (doc.mobile_no or "").strip()
	if not mobile:
		return
	company = doc.custom_company
	if not company:
		return
	existing = frappe.db.sql(
		"""
		SELECT name, customer_name
		FROM `tabCustomer`
		WHERE mobile_no = %s
		  AND custom_company = %s
		  AND disabled = 0
		  AND name != %s
		LIMIT 1
		""",
		(mobile, company, doc.name or ""),
		as_dict=True,
	)
	if existing:
		e = existing[0]
		frappe.throw(
			title=_("Duplicate Mobile Number"),
			msg=_(
				"A customer in this company already uses the mobile number <b>{0}</b>: "
				"<b>{1}</b>. Select that customer instead of creating a duplicate."
			).format(mobile, e["customer_name"] or e["name"]),
		)


def validate_employee_pin(doc, method):
	pin = (doc.custom_pos_pin or "").strip()

	if not pin:
		return

	# Format: digits only, 4–6 characters
	if not re.fullmatch(r"\d{4,6}", pin):
		frappe.throw(
			"POS PIN must be <b>4 to 6 digits only</b> (no letters or special characters).",
			title="Invalid PIN",
		)

	# Uniqueness per company — Employee → Branch → custom_pos_company
	if not doc.branch:
		return

	company = frappe.db.get_value("Branch", doc.branch, "custom_pos_company")
	if not company:
		return

	duplicate = frappe.db.sql(
		"""
		SELECT e.name, e.employee_name
		FROM `tabEmployee` e
		INNER JOIN `tabBranch` b ON b.name = e.branch
		WHERE b.custom_pos_company = %s
		  AND e.custom_pos_pin = %s
		  AND e.name != %s
		LIMIT 1
		""",
		(company, pin, doc.name or "__new__"),
		as_dict=True,
	)

	if duplicate:
		frappe.throw(
			f"PIN <b>{pin}</b> is already assigned to employee "
			f"<b>{duplicate[0]['employee_name']}</b> ({duplicate[0]['name']}) "
			f"in company <b>{company}</b>. Each employee in a company must have a unique PIN.",
			title="Duplicate PIN",
		)


# POS Profile account fields — server-side mirror of the set_query filters in
# public/js/pos_profile.js. The client filters and the Custom Field link_filters
# only restrict the UI dropdown; they are NOT enforced on save. This guarantees
# the same rules for API / Data Import / console writes.
#
# Each entry: fieldname -> (label, conditions). A condition is a (column, op, value)
# tuple checked against the linked Account. {company} is substituted at runtime.
SALARY_ADVANCE_FIELD = "custom_salary_advance_account"

# NOTE on the salary-advance account (report item HIGH 4, 2026-07-21):
# a guard here used to also reject the company's own default receivable ("Debtors"),
# because a staff advance posted there lands in CUSTOMER receivables and skews the AR
# aging report. Removed 2026-07-23 by the owner's decision: the account type is the
# constraint we enforce, and WHICH receivable account to use is the accountant's call,
# not ours. If AR aging ever shows staff advances again, this is why — the fix is to
# pick a dedicated employee-advances account on the POS Profile, not to re-add a rule.

POS_PROFILE_ACCOUNT_RULES = {
	"custom_cash_account": (
		"Cash Drawer Account",
		[("account_type", "=", "Cash")],
	),
	SALARY_ADVANCE_FIELD: (
		"Salary Advance Account",
		[("account_type", "=", "Receivable")],
	),
	"custom_expense_account": (
		"Expense Account",
		[("root_type", "=", "Expense")],
	),
	"custom_owner_deposit_account": (
		"Owner Deposit Account",
		[
			("root_type", "in", ("Equity", "Liability")),
			("account_type", "not in", ("Receivable", "Payable")),
		],
	),
	"custom_bank_account": (
		"Bank Account",
		[("account_type", "=", "Bank")],
	),
}


def validate_pos_profile_accounts(doc, method):
	for fieldname, (label, conditions) in POS_PROFILE_ACCOUNT_RULES.items():
		account = doc.get(fieldname)
		if not account:
			continue

		acc = frappe.db.get_value(
			"Account",
			account,
			["account_type", "root_type", "company", "is_group"],
			as_dict=True,
		)
		if not acc:
			frappe.throw(
				f"<b>{label}</b>: account <b>{account}</b> does not exist.",
				title="Invalid Account",
			)

		if acc.is_group:
			frappe.throw(
				f"<b>{label}</b> must be a ledger account, not a group account "
				f"(<b>{account}</b> is a group).",
				title="Invalid Account",
			)

		if doc.company and acc.company != doc.company:
			frappe.throw(
				f"<b>{label}</b> (<b>{account}</b>) belongs to company "
				f"<b>{acc.company}</b>, but this POS Profile is for "
				f"<b>{doc.company}</b>.",
				title="Company Mismatch",
			)

		for column, op, value in conditions:
			actual = acc.get(column)
			if op == "=" and actual != value:
				ok = False
			elif op == "in" and actual not in value:
				ok = False
			elif op == "not in" and actual in value:
				ok = False
			else:
				ok = True

			if not ok:
				expected = value if op == "=" else f"{op} {list(value)}"
				frappe.throw(
					f"<b>{label}</b> (<b>{account}</b>) has {column} "
					f"<b>{actual}</b>, but it must be <b>{expected}</b>.",
					title="Invalid Account",
				)


def validate_loyalty_program_tier_names(doc, method):
	"""No two tiers in one program may share a name.

	ERPNext allows it; the POS cannot survive it. Its local `loyalty_tiers` table
	is keyed on (site_url, program, tier_name) and every program is written in one
	transaction, so a single duplicate rolls back the whole set and — being a
	SQLite error rather than a network one — never retries. One mistyped tier name
	takes a till's entire loyalty sync down permanently.

	Compared trimmed and case-insensitively: `VIP` beside `vip ` is a typo, not a
	second tier.
	"""
	names = [row.tier_name for row in (doc.get("collection_rules") or [])]
	duplicate = first_duplicate_tier_name(names)
	if not duplicate:
		return
	frappe.throw(
		title=_("Duplicate Tier Name"),
		msg=_(
			"This loyalty program already has a tier named <b>{0}</b>. "
			"Give each tier its own name."
		).format(duplicate),
	)


def employee_branches(employee: str) -> list[str]:
	"""Every branch an employee works at — the native `branch` plus the POS ones.

	ERPNext's Employee carries a single `branch` link, so Barakat stores the rest
	in the `custom_pos_branches` child table and keeps `branch` pointed at the
	first one for native branch filtering (payroll, reports). Callers that ask
	"may this employee be recorded against branch X?" must consult both.
	"""
	native = frappe.db.get_value("Employee", employee, "branch")
	rows = frappe.get_all(
		"POS Employee Branch",
		filters={"parent": employee, "parenttype": "Employee"},
		pluck="branch",
	)
	branches = [b for b in rows if b]
	if native and native not in branches:
		branches.append(native)
	return branches


def validate_attendance_branch(doc, method):
	"""An attendance record may only name a branch its employee actually works at.

	`custom_branch` exists because native Attendance has no branch field at all —
	only company and department, both fetched read-only from the Employee. It is
	descriptive ("where he worked that day"), never a second dimension of the
	record: ERPNext's own `validate_duplicate_record` still allows exactly one
	attendance per employee per date, so this cannot be used to mark someone
	present in one branch and absent in another on the same day.

	Left empty the record simply doesn't say which branch — which is the honest
	answer for staff who have no branch assigned at all.
	"""
	branch = (doc.get("custom_branch") or "").strip()
	if not branch:
		return

	allowed = employee_branches(doc.employee)
	if branch in allowed:
		return

	frappe.throw(
		title=_("Wrong Branch"),
		msg=_(
			"<b>{0}</b> does not work at <b>{1}</b>. Assign the branch on the "
			"employee first, or record the attendance against one of their "
			"branches."
		).format(doc.employee_name or doc.employee, branch),
	)
