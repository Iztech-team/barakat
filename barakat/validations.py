import re

import frappe
from frappe import _

from barakat import receipt_logo
from barakat.loyalty_tier_names import first_duplicate_tier_name
from barakat.permissions import pin_role_decision
from barakat.tax_template_rows import first_duplicate_account


class PosPinRoleError(frappe.ValidationError):
	"""Raised when a PIN is being given to a persona that cannot operate a till.

	Its own class so the proxy can recognise it by `exc_type` rather than matching
	the message text, which Frappe translates per user language.
	"""


class DuplicateTaxAccountError(frappe.ValidationError):
	"""Raised when two rows of one tax template post to the same account.

	Its own class so the proxy can recognise it by `exc_type` and answer with a
	coded, localized message. Frappe TRANSLATES thrown messages per user
	language, so matching the text works in English and silently stops working
	the moment an Arabic or Hebrew user hits it.
	"""


class POSProfileWarehouseLocked(frappe.ValidationError):
	"""Raised when a POS Profile's warehouse is changed during an open shift.

	A dedicated class so the proxy can recognise this by `exc_type` instead of
	matching the message text. Frappe TRANSLATES thrown messages per user
	language, so a text match works in English and silently stops working the
	moment an Arabic or Hebrew operator hits it — and the caller then falls back
	to a generic "something went wrong" toast that explains nothing.
	"""


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


def open_shifts_for_pos_profile(pos_profile):
	"""Open POS Opening Entries bound to ONE profile.

	Narrower than open_shifts_for_company on purpose: a profile's warehouse only
	governs the tills using that profile, so another profile's open shift in the
	same company must not block the edit.
	"""
	if not pos_profile:
		return []
	return frappe.db.sql(
		"""
		SELECT name, pos_profile, company
		FROM `tabPOS Opening Entry`
		WHERE status = 'Open'
		  AND pos_profile = %s
		LIMIT 5
		""",
		(pos_profile,),
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


def _employee_companies(doc):
	"""Every company this employee belongs to, by either link that names one.

	There are two, and they used to be read by two different layers:

	  * `Employee.company` — what the admin panel stamps from the active tenant,
	    and the only field its own duplicate check filters on.
	  * `Branch.custom_pos_company` — what this rule read, alone, until now.

	They normally agree. When they don't, the two layers disagreed about what
	"same company" meant, so a PIN one of them refused the other allowed. Taking
	the union settles it in the safe direction for a login credential: we would
	rather refuse a PIN that was technically free than let two people in one
	company share one.

	Returns an empty set when neither link resolves — nothing to scope against,
	so the caller lets the save through rather than guessing.
	"""
	companies = set()

	if doc.company:
		companies.add(doc.company)

	if doc.branch:
		branch_company = frappe.db.get_value("Branch", doc.branch, "custom_pos_company")
		if branch_company:
			companies.add(branch_company)

	return companies


def _system_context():
	"""True during install, migrate or a patch — when nothing may be refused."""
	return bool(frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch)


def enforce_pin_role(doc, method=None):
	"""A POS PIN may exist only on a Manager, Branch Supervisor or Cashier.

	Wired on Employee `validate`, and deliberately FIRST — ahead of
	`validate_employee_pin` — so the uniqueness check never fires on a PIN that is
	about to be cleared. Getting that order wrong would refuse a perfectly legal
	role change because the PIN being discarded happened to clash with somebody.

	The decision itself lives in `barakat.permissions.pin_role_decision` so it is
	readable and testable without a bench; this function only knows about documents.

	`doc.get(...)` rather than attribute access throughout: a site whose custom
	fields have not synced has no `custom_pos_pin` at all (izdehar today), and an
	AttributeError here would break every Employee save on it.

	**Permlevel, and why there is no second guard here.** `custom_pos_pin` sits at
	permlevel 1, so the obvious worry is that Frappe restores the stored value for a
	caller who may not write the field — silently reverting this clear and leaving
	the bug open with every test still green. It does not: `Document.save()` runs
	`validate_higher_perm_levels()` BEFORE `run_before_save_methods()`, so this hook
	is the last word. Measured on the bench, not reasoned about — an `HR Manager`
	caller with `get_permlevel_access("write") == [0]` cleared the PIN of a
	pre-existing HR record, and still did so with a proposed `on_update` backstop
	removed. The backstop was deleted for being decoration. If a future Frappe
	reorders those two calls, this stops working silently; the bench probe in
	`docs/superpowers/specs/2026-08-17-pos-pin-role-lifecycle-design.md` is how to
	re-check it.
	"""
	preset = doc.get("custom_role_preset")
	pin = doc.get("custom_pos_pin")

	stored = None
	if doc.name:
		# The DB layer, not the document: this must read what is actually on record
		# even when the caller cannot see the field.
		stored = frappe.db.get_value("Employee", doc.name, "custom_pos_pin")

	decision = pin_role_decision(preset, pin, stored, system_context=_system_context())

	if decision == "keep":
		return

	if decision == "refuse":
		frappe.throw(
			_(
				"A POS PIN can only be given to a Manager, Branch Supervisor or Cashier."
			),
			PosPinRoleError,
			title=_("Not a till role"),
		)

	doc.custom_pos_pin = ""


def validate_employee_pin(doc, method):
	pin = (doc.custom_pos_pin or "").strip()

	if not pin:
		return

	# Judge only what this save actually changes. If the PIN and both company
	# links are untouched, the verdict cannot legitimately differ from the one
	# already on record — re-deciding it would fail an edit to the SALARY or the
	# status over a PIN the user never typed and cannot see from that screen.
	#
	# This matters because the rule below is stricter than the one these records
	# were saved under (a branchless employee escaped the uniqueness check
	# entirely). Live test sites already hold such pairs. Without this guard,
	# tightening the rule would retroactively freeze every one of those employees
	# — the precise failure mode that took prod down on 2026-07-28.
	#
	# All three fields are compared, not just the PIN: moving an employee to
	# another branch or company can create a clash without the PIN changing at
	# all, and that save must still be judged.
	if doc.name:
		before = frappe.db.get_value(
			"Employee",
			doc.name,
			["custom_pos_pin", "branch", "company"],
			as_dict=True,
		)
		if (
			before
			and (before.custom_pos_pin or "").strip() == pin
			and before.branch == doc.branch
			and before.company == doc.company
		):
			return

	# Format: digits only, 4–6 characters
	if not re.fullmatch(r"\d{4,6}", pin):
		frappe.throw(
			"POS PIN must be <b>4 to 6 digits only</b> (no letters or special characters).",
			title="Invalid PIN",
		)

	# Uniqueness per company. Which company an employee belongs to is reachable
	# two ways, and this rule honours BOTH (see _employee_companies): reading only
	# the branch link used to skip the check entirely for a branchless employee,
	# so two of them could share a PIN — and a shared PIN becomes a real POS login
	# collision the moment either one is put on a branch.
	companies = _employee_companies(doc)
	if not companies:
		return

	# At most two entries by construction (own company, branch's company), so pad
	# to a fixed pair: the query shape stays constant and no SQL is string-built.
	company_list = sorted(companies)
	company_a, company_b = company_list[0], company_list[-1]

	duplicate = frappe.db.sql(
		"""
		SELECT e.name, e.employee_name, e.company AS emp_company,
		       b.custom_pos_company AS branch_company
		FROM `tabEmployee` e
		LEFT JOIN `tabBranch` b ON b.name = e.branch
		WHERE e.custom_pos_pin = %s
		  AND e.name != %s
		  AND (e.company IN (%s, %s) OR b.custom_pos_company IN (%s, %s))
		LIMIT 1
		""",
		(
			pin,
			doc.name or "__new__",
			company_a,
			company_b,
			company_a,
			company_b,
		),
		as_dict=True,
	)

	if duplicate:
		d = duplicate[0]
		# Name the company the two actually share, not just whichever we read
		# first — with a divergent record those can be different companies.
		clash = next(
			(c for c in (d["emp_company"], d["branch_company"]) if c in companies),
			company_a,
		)
		# Deliberately says neither the PIN nor whose it is.
		#
		# This message used to read "PIN <b>4471</b> is already assigned to
		# <b>Ahmad</b>", which made the save form a PIN oracle: anyone who could
		# edit an Employee could type candidate PINs and be told, one at a time,
		# exactly which colleague owned each one. Guessing a manager's PIN that
		# way needed no database access and no permissions beyond editing a single
		# employee record.
		#
		# The person hitting this is choosing a new PIN, and "pick another" is all
		# they need. The clash is logged for anyone genuinely diagnosing it.
		frappe.logger("barakat").info(
			f"duplicate POS PIN rejected for {doc.name or 'new employee'}: "
			f"clashes with {d['name']} in {clash}"
		)
		frappe.throw(
			"This PIN is already in use by another employee in this company. "
			"Please choose a different PIN.",
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


def validate_tax_template_accounts(doc, method):
	"""No two rows of one tax template may post to the same account.

	ERPNext allows it and then gets the arithmetic wrong — see
	`barakat.tax_template_rows` for the invoice this was found on. The template
	is the last point where the mistake costs nothing to fix; after it, a till
	has already taken money that the invoice does not account for.

	On the doctype rather than in the Admin Panel alone, because the desk, the
	REST API and any future caller write this document too.

	Compared trimmed and case-insensitively: an Account docname is unique under
	MariaDB's default collation, so `VAT - BAM` beside `vat - BAM` is one
	account written two ways, not two accounts.
	"""
	accounts = [row.account_head for row in (doc.get("taxes") or [])]
	duplicate = first_duplicate_account(accounts)
	if not duplicate:
		return
	frappe.throw(
		title=_("Duplicate Tax Account"),
		msg=_(
			"This tax template already has a row on <b>{0}</b>. Each row must post "
			"to a different account, or the tax on every sale is calculated wrongly."
		).format(duplicate),
		exc=DuplicateTaxAccountError,
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


def validate_pos_profile_warehouse_change(doc, method):
	"""A till stores ONE stock quantity per product, with no warehouse dimension.

	Repointing the profile mid-shift means every quantity the cashier can see
	still describes the OLD branch until the next stock sync lands, and anything
	the new warehouse does not stock keeps the old branch's number entirely — so
	the till reports stock it does not have, in the middle of selling.

	The POS forgets those quantities when it notices the change, but it only
	notices on its next sync. Between the edit and that sync the till is wrong,
	and a shift is exactly when being wrong costs money.
	"""
	# `validate` also runs on insert, and Frappe assigns doc.name before
	# run_before_save_methods() — so a new profile already has a name and the
	# lookup below returns None, which would read as "the warehouse changed".
	# A brand-new profile cannot have an open shift anyway.
	if doc.is_new():
		return

	previous = frappe.db.get_value("POS Profile", doc.name, "warehouse")
	if previous == doc.warehouse:
		return

	open_shifts = open_shifts_for_pos_profile(doc.name)
	if not open_shifts:
		return

	frappe.throw(
		title=_("Cannot Change Warehouse"),
		exc=POSProfileWarehouseLocked,
		msg=_(
			"You cannot change this POS Profile's warehouse from <b>{0}</b> to "
			"<b>{1}</b> while a shift is open on it. The till is still selling "
			"against the old warehouse's stock. Please close the shift first:"
			"<ul>{2}</ul>"
		).format(
			previous or _("(none)"),
			doc.warehouse or _("(none)"),
			_shift_lines(open_shifts),
		),
	)


class ReceiptLogoInvalid(frappe.ValidationError):
	"""Raised when a POS Profile's receipt logo cannot be stored.

	Its own class so the proxy can recognise it by `exc_type` rather than by
	matching the message text — Frappe translates thrown messages per user
	language, so a text match works in English and silently stops working the
	moment an Arabic or Hebrew manager hits it.
	"""


def validate_pos_profile_receipt_logo(doc, method):
	"""Normalise and check the three receipt-logo fields on a POS Profile.

	This is the LAST word on those fields, not a convenience. The Admin Panel is
	where a manager prepares a logo, but the desk and the raw REST API write the
	same document without going anywhere near the panel's own checks — so the
	size ceiling, the PNG-only rule and "Custom needs an image" have to live
	here to mean anything.

	A profile saved before the feature existed carries none of the three fields.
	It must keep saving, and it must keep printing what it prints today, so an
	absent mode reads as `Default` and an absent width as 32 (the `W * 0.32` the
	till already draws) rather than as an error or as zero.
	"""
	try:
		mode, image, width = receipt_logo.resolve(
			doc.get("custom_receipt_logo_mode"),
			doc.get("custom_receipt_logo"),
			doc.get("custom_receipt_logo_width"),
		)
	except receipt_logo.ReceiptLogoError as err:
		frappe.throw(
			title=_("Receipt Logo"),
			exc=ReceiptLogoInvalid,
			msg=_(str(err)),
		)
		return

	# Written back so the stored document always matches what was validated:
	# `Default`/`None` drop the image bytes, and an out-of-range width is
	# persisted already clamped rather than clamped again by every reader.
	doc.custom_receipt_logo_mode = mode
	doc.custom_receipt_logo = image
	doc.custom_receipt_logo_width = width
