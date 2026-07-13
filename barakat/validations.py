import re

import frappe
from frappe import _


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
				"<b>{1}</b> ({2}). Select that customer instead of creating a duplicate."
			).format(mobile, e["customer_name"] or e["name"], e["name"]),
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
POS_PROFILE_ACCOUNT_RULES = {
	"custom_cash_account": (
		"Cash Drawer Account",
		[("account_type", "=", "Cash")],
	),
	"custom_salary_advance_account": (
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
