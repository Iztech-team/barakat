import frappe
from frappe import _
from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import get_payments, get_taxes


@frappe.whitelist()
def get_shift_invoices(opening_entry: str) -> dict:
	"""Unconsolidated POS Invoices for a shift, whoever rang them up.

	ERPNext's own `pos_closing_entry.get_invoices` filters by invoice `owner`, so
	when two accounts sold on one shift it returns only one account's invoices
	and the rest are never consolidated — they simply vanish from the books.

	Barakat scopes by the POS Profile and the opening entry's OWN period
	instead. A profile is claimed by one device at a time
	(`POS Profile.custom_device`, enforced in `barakat.api.device.select_profile`),
	so that pair identifies one till's trade without needing the owner.

	Taking the window from the opening entry rather than from the caller also
	stops invoices being orphaned when the device's local record disagrees about
	when the shift began — a device that adopted an already-open shift records
	its own, later start time.

	Returns the same {invoices, payments, taxes} shape as ERPNext's version, so
	it is a drop-in replacement for the POS.
	"""
	if not opening_entry:
		frappe.throw(_("opening_entry is required."))

	opening = frappe.db.get_value(
		"POS Opening Entry",
		opening_entry,
		["pos_profile", "period_start_date"],
		as_dict=True,
	)
	if not opening:
		frappe.throw(_("POS Opening Entry {0} not found.").format(opening_entry))

	invoices = frappe.db.sql(
		"""
		SELECT name, customer, posting_date, posting_time, grand_total, net_total,
		       total_qty, total_taxes_and_charges, is_return, return_against,
		       'POS Invoice' AS invoice_type,
		       TIMESTAMP(posting_date, posting_time) AS timestamp
		FROM `tabPOS Invoice`
		WHERE docstatus = 1
		  AND IFNULL(consolidated_invoice, '') = ''
		  AND pos_profile = %(profile)s
		  AND TIMESTAMP(posting_date, posting_time) >= %(start)s
		ORDER BY TIMESTAMP(posting_date, posting_time)
		""",
		{"profile": opening.pos_profile, "start": opening.period_start_date},
		as_dict=True,
	)

	return {
		"invoices": invoices,
		"payments": get_payments(invoices),
		"taxes": get_taxes(invoices),
	}


@frappe.whitelist()
def get_shift_summary(opening_entry_name: str) -> dict:
	"""
	Returns aggregate cash summary for a POS Opening Entry.
	Used by the desktop app when local DB is wiped and user wants to close the shift.
	"""
	if not opening_entry_name:
		frappe.throw(_("opening_entry_name is required."))

	opening = frappe.get_doc("POS Opening Entry", opening_entry_name)

	cash_modes = get_cash_modes(opening.pos_profile)

	opening_cash = 0.0
	for row in (opening.balance_details or []):
		if row.mode_of_payment in cash_modes:
			opening_cash = float(row.opening_amount or 0)
			break

	# Every tender on this profile's invoices, kept split by mode: the drawer
	# takes only the cash-typed ones, and the rest are reported so the till can
	# show what the shift sold rather than leaving its card takings invisible.
	rows = frappe.db.sql("""
		SELECT pi.name, pi.is_return,
			sip.mode_of_payment, COALESCE(sip.amount, 0) AS amount
		FROM `tabPOS Invoice` pi
		LEFT JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
		WHERE pi.pos_profile = %(pos_profile)s
		  AND pi.docstatus = 1
		  AND pi.posting_date >= %(start_date)s
	""", {"pos_profile": opening.pos_profile, "start_date": opening.period_start_date}, as_dict=True)

	invoices = {}
	cash_sales = 0.0
	cash_refunds = 0.0
	non_cash_sales: dict = {}
	for row in rows:
		invoices[row.name] = bool(row.is_return)
		mode = row.mode_of_payment
		if not mode:
			continue
		amount = float(row.amount or 0)
		if mode in cash_modes:
			if row.is_return:
				cash_refunds += abs(amount)
			else:
				cash_sales += amount
		elif not row.is_return:
			non_cash_sales[mode] = non_cash_sales.get(mode, 0.0) + amount

	# Cash movements from Journal Entries linked to this opening entry
	journals = frappe.db.sql("""
		SELECT je.name, je.user_remark,
			jea.debit_in_account_currency,
			jea.credit_in_account_currency,
			jea.account
		FROM `tabJournal Entry` je
		INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		WHERE je.custom_pos_opening_entry = %(opening)s
		  AND je.docstatus = 1
	""", {"opening": opening_entry_name}, as_dict=True)

	# The cash drawer side is the credit on cash-out, debit on cash-in.
	# We aggregate by journal entry (one JE per movement) using the cash account rows.
	cash_account = frappe.db.get_value("POS Profile", opening.pos_profile, "custom_cash_account")
	cash_in = 0.0
	cash_out = 0.0
	seen_journals = set()
	for row in journals:
		if row.name in seen_journals:
			continue
		if row.account != cash_account:
			continue
		seen_journals.add(row.name)
		debit = float(row.debit_in_account_currency or 0)
		credit = float(row.credit_in_account_currency or 0)
		if debit > 0:
			cash_in += debit
		elif credit > 0:
			cash_out += credit

	expected_total = opening_cash + cash_sales - cash_refunds + cash_in - cash_out

	# Largest first, mirroring the till's own breakdown.
	non_cash = sorted(
		(
			{"mode_of_payment": mode, "amount": amount}
			for mode, amount in non_cash_sales.items()
			if amount
		),
		key=lambda row: row["amount"],
		reverse=True,
	)
	non_cash_total = sum(row["amount"] for row in non_cash)

	return {
		"opening_cash": opening_cash,
		"cash_sales": cash_sales,
		"cash_refunds": cash_refunds,
		"cash_in": cash_in,
		"cash_out": cash_out,
		"expected_total": expected_total,
		"orders_count": len([name for name, is_return in invoices.items() if not is_return]),
		"non_cash_sales": non_cash,
		"non_cash_total": non_cash_total,
		"total_sales": cash_sales + non_cash_total,
	}


def get_cash_modes(pos_profile: str) -> set:
	"""The profile's Modes of Payment whose ERPNext `type` is Cash.

	By TYPE, never by name. This compared `mode_of_payment == "Cash"` literally,
	so a company whose cash mode is called "نقدي" got an opening balance of 0 and
	cash sales of 0 from this endpoint — and this endpoint is the one a till
	falls back on when its local database is gone and it is trying to close the
	shift, so the cashier was told they were over by the entire day's takings.

	The POS fixed the same bug on its own side some time ago; this is the copy
	that was left behind.
	"""
	modes = frappe.get_all(
		"POS Payment Method",
		filters={"parent": pos_profile, "parenttype": "POS Profile"},
		pluck="mode_of_payment",
	)
	if not modes:
		return set()
	return set(
		frappe.get_all(
			"Mode of Payment",
			filters={"name": ["in", modes], "type": "Cash"},
			pluck="name",
		)
	)


@frappe.whitelist()
def get_shift_orders(opening_entry_name: str) -> dict:
	"""
	Returns all POS Invoices and Journal Entries for a shift so the desktop app
	can restore local records after a DB wipe and resume the shift.
	"""
	if not opening_entry_name:
		frappe.throw(_("opening_entry_name is required."))

	opening = frappe.get_doc("POS Opening Entry", opening_entry_name)

	# Fetch POS Invoices
	invoices = frappe.db.sql("""
		SELECT
			pi.name, pi.customer, pi.pos_profile, pi.currency,
			pi.net_total, pi.grand_total, pi.discount_amount,
			pi.is_return, pi.return_against,
			pi.posting_date, pi.creation,
			pi.custom_external_id, pi.custom_operator_employee,
			pi.owner
		FROM `tabPOS Invoice` pi
		WHERE pi.pos_profile = %(pos_profile)s
		  AND pi.docstatus = 1
		  AND pi.posting_date >= %(start_date)s
		ORDER BY pi.creation ASC
	""", {"pos_profile": opening.pos_profile, "start_date": opening.period_start_date}, as_dict=True)

	# Fetch items + payments per invoice
	invoice_names = [inv.name for inv in invoices]
	items_by_invoice: dict = {}
	payments_by_invoice: dict = {}

	if invoice_names:
		items = frappe.db.sql("""
			SELECT parent, item_code, item_name, qty, rate, amount,
				discount_percentage, discount_amount
			FROM `tabPOS Invoice Item`
			WHERE parent IN %(names)s
		""", {"names": invoice_names}, as_dict=True)
		for item in items:
			items_by_invoice.setdefault(item.parent, []).append(item)

		payments = frappe.db.sql("""
			SELECT parent, mode_of_payment, amount
			FROM `tabSales Invoice Payment`
			WHERE parent IN %(names)s
		""", {"names": invoice_names}, as_dict=True)
		for payment in payments:
			payments_by_invoice.setdefault(payment.parent, []).append(payment)

	orders = []
	for inv in invoices:
		orders.append({
			"name": inv.name,
			"customer": inv.customer,
			"pos_profile": inv.pos_profile,
			"currency": inv.currency,
			"net_total": float(inv.net_total or 0),
			"grand_total": float(inv.grand_total or 0),
			"discount_amount": float(inv.discount_amount or 0),
			"is_return": bool(inv.is_return),
			"return_against": inv.return_against,
			"posting_date": str(inv.posting_date),
			"creation": str(inv.creation),
			"external_id": inv.custom_external_id,
			"operator_employee": inv.custom_operator_employee,
			"owner": inv.owner,
			"items": [
				{
					"item_code": it.item_code,
					"item_name": it.item_name,
					"qty": float(it.qty or 0),
					"rate": float(it.rate or 0),
					"amount": float(it.amount or 0),
					"discount_percentage": float(it.discount_percentage or 0),
					"discount_amount": float(it.discount_amount or 0),
				}
				for it in items_by_invoice.get(inv.name, [])
			],
			"payments": [
				{
					"mode_of_payment": p.mode_of_payment,
					"amount": float(p.amount or 0),
				}
				for p in payments_by_invoice.get(inv.name, [])
			],
		})

	# Fetch Journal Entries (cash movements)
	journals = frappe.db.sql("""
		SELECT je.name, je.user_remark, je.posting_date, je.creation,
			je.custom_external_id
		FROM `tabJournal Entry` je
		WHERE je.custom_pos_opening_entry = %(opening)s
		  AND je.docstatus = 1
		ORDER BY je.creation ASC
	""", {"opening": opening_entry_name}, as_dict=True)

	cash_account = frappe.db.get_value(
		"POS Profile", opening.pos_profile, "custom_cash_account"
	)
	movements = []
	for je in journals:
		accounts = frappe.db.get_all(
			"Journal Entry Account",
			filters={"parent": je.name},
			fields=["account", "debit_in_account_currency", "credit_in_account_currency"],
		)
		# Determine direction from the cash account row
		direction = None
		amount = 0.0
		for acc in accounts:
			if acc.account == cash_account:
				if float(acc.debit_in_account_currency or 0) > 0:
					direction = "in"
					amount = float(acc.debit_in_account_currency)
				elif float(acc.credit_in_account_currency or 0) > 0:
					direction = "out"
					amount = float(acc.credit_in_account_currency)
				break
		if direction is None:
			continue

		# Parse category from user_remark: "[POS Category] reason"
		remark = je.user_remark or ""
		category = "Other"
		reason = remark
		if remark.startswith("[POS ") and "]" in remark:
			end = remark.index("]")
			category = remark[5:end].strip()
			reason = remark[end + 1:].strip()

		movements.append({
			"name": je.name,
			"external_id": je.custom_external_id,
			"direction": direction,
			"amount": amount,
			"category": category,
			"reason": reason,
			"posting_date": str(je.posting_date),
			"creation": str(je.creation),
		})

	return {
		"opening_entry": opening_entry_name,
		"pos_profile": opening.pos_profile,
		"period_start_date": str(opening.period_start_date),
		"opening_cash": float(
			next(
				(r.opening_amount for r in opening.balance_details if r.mode_of_payment == "Cash"),
				0,
			)
		),
		"orders": orders,
		"movements": movements,
	}
