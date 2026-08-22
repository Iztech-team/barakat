"""What a customer may still buy on credit, and what they already owe.

One endpoint, read by two callers: the till (before it offers دين) and the
Admin Panel (to show a balance beside the limit). Both ask the SERVER rather
than working it out themselves, because the answer has to match the guard in
`barakat.overrides.pos_invoice` exactly — a till that computed a more generous
headroom than the guard allows would offer credit that is then refused at push
time, which is the drift this endpoint exists to prevent.

See docs/superpowers/specs/2026-08-17-pos-credit-sales-design.md.
"""

import frappe
from frappe import _

from barakat.credit_limits import credit_headroom, credit_limit_of, total_owed
from barakat.overrides.pos_invoice import customer_credit_limit, customer_debt


@frappe.whitelist()
def get_customer_credit(customer, company):
	"""This customer's credit standing with this company.

	`owed` is the sum that matters: consolidated debt in the GL PLUS submitted
	POS Invoices that have not been merged yet. The two are also returned
	separately because "you owe ₪300, ₪200 of it from today" is the difference
	between a cashier believing the number and not.
	"""
	if not customer or not company:
		frappe.throw(_("Customer and company are both required."))

	# read permission on the customer is the right gate: anyone who may see the
	# customer may see what they owe, and nobody else may.
	if not frappe.has_permission("Customer", doc=customer):
		frappe.throw(_("Not permitted to read this customer."), frappe.PermissionError)

	precision = frappe.get_precision("POS Invoice", "grand_total") or 2
	limit = credit_limit_of(customer_credit_limit(customer, company))
	consolidated, unconsolidated = customer_debt(customer, company)

	return {
		"customer": customer,
		"company": company,
		"currency": frappe.get_cached_value("Company", company, "default_currency"),
		"limit": limit,
		"consolidated": consolidated,
		"unconsolidated": unconsolidated,
		"owed": total_owed(consolidated, unconsolidated, precision),
		"headroom": credit_headroom(limit, consolidated, unconsolidated, precision),
		# The till reads this rather than comparing limit to 0 itself, so the
		# "0 means none, not unlimited" rule has exactly one home.
		"mayTakeCredit": limit > 0,
	}


@frappe.whitelist()
def set_customer_credit_limit(customer, company, credit_limit):
	"""Set (or clear) a customer's credit limit for one company.

	Writes ERPNext's own `Customer Credit Limit` child row rather than a custom
	field, so the limit the Admin Panel sets is the same one ERPNext's
	`check_credit_limit` reads at consolidation.

	A limit of 0 REMOVES the row — under this feature's rules zero and absent
	both mean "no credit", and leaving a zero row behind would suggest a
	configured ceiling that happens to be nothing.
	"""
	if not frappe.has_permission("Customer", ptype="write", doc=customer):
		frappe.throw(_("Not permitted to change this customer."), frappe.PermissionError)

	limit = credit_limit_of(credit_limit)
	doc = frappe.get_doc("Customer", customer)
	existing = [row for row in (doc.credit_limits or []) if row.company == company]

	if limit <= 0:
		for row in existing:
			doc.remove(row)
	elif existing:
		existing[0].credit_limit = limit
	else:
		doc.append("credit_limits", {"company": company, "credit_limit": limit})

	doc.save()
	return get_customer_credit(customer, company)


def mode_deposit_account(mode_of_payment, company, pos_profile=None):
	"""Where money taken through this payment method belongs.

	The mode's OWN account first, so a card repayment lands with the card
	takings rather than in the drawer. Getting this wrong is not cosmetic: the
	drawer would be reported as holding money that is really at the bank, and
	the cashier would be asked to account for notes nobody ever handed them.

	The POS Profile's cash account is the fallback and only for a CASH-type
	mode — that account IS the physical drawer, and naming it for a card would
	reintroduce exactly the error above.
	"""
	from erpnext.accounts.doctype.journal_entry.journal_entry import (
		get_default_bank_cash_account,
	)

	account = frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": mode_of_payment, "company": company},
		"default_account",
	)
	if account:
		return account

	mode_type = frappe.db.get_value("Mode of Payment", mode_of_payment, "type")
	if mode_type == "Cash" and pos_profile:
		till_cash = frappe.db.get_value(
			"POS Profile", pos_profile, "custom_cash_account"
		)
		if till_cash:
			return till_cash

	# Last resort: whatever ERPNext itself would pick for this mode.
	resolved = get_default_bank_cash_account(
		company, "Cash" if mode_type == "Cash" else "Bank", mode_of_payment=mode_of_payment
	)
	return (resolved or {}).get("account")


@frappe.whitelist()
def record_customer_repayment(
	customer,
	company,
	amount,
	mode_of_payment,
	pos_profile=None,
	external_id=None,
	pos_opening_entry=None,
):
	"""Take money off what a customer owes, as a submitted Payment Entry.

	ONLINE ONLY, by design, and the till enforces that too: a repayment cannot
	be queued the way a sale can. A queued repayment would let a customer pay
	the same debt twice on two tills, and would let the cap below be evaluated
	against a debt that has since changed.

	The cap is recomputed HERE against a freshly-read debt and never taken from
	the caller. The till's figure is a snapshot: between fetching it and taking
	the money, another till may have sold to the same customer, or a refund may
	have cancelled part of it.

	Allocation is oldest invoice first, and only against CONSOLIDATED invoices —
	a POS Invoice from a still-open shift writes no GL and ERPNext will not let
	a Payment Entry reference one. Whatever is left over is held on the
	customer's account and reconciles itself when the shift consolidates.
	"""
	from erpnext.accounts.party import get_party_account

	from barakat.credit_repayment import allocate_repayment, valid_repayment

	if not customer or not company:
		frappe.throw(_("Customer and company are both required."))
	if not mode_of_payment:
		frappe.throw(_("A mode of payment is required."))

	# Taking money against a customer's account is a write, so read permission
	# on the customer is not enough on its own.
	if not frappe.has_permission("Customer", doc=customer):
		frappe.throw(_("Not permitted to read this customer."), frappe.PermissionError)
	if not frappe.has_permission("Payment Entry", ptype="create"):
		frappe.throw(_("Not permitted to record payments."), frappe.PermissionError)

	precision = frappe.get_precision("POS Invoice", "grand_total") or 2
	try:
		paid = float(amount)
	except (TypeError, ValueError):
		frappe.throw(_("The amount is not a number."))

	# A dropped response is indistinguishable from a refusal, so a cashier who
	# presses again after a timeout would otherwise take the customer's money
	# twice. The till generates this key ONCE per attempt at the dialog, not per
	# press, and a second call carrying it returns the first entry untouched.
	#
	# Checked before the cap below rather than after: the debt has already moved
	# by the time a retry arrives, so re-validating a duplicate would refuse it
	# as "more than they owe" and leave the cashier believing nothing happened.
	if external_id:
		existing = frappe.db.get_value(
			"Payment Entry",
			{"custom_external_id": external_id, "docstatus": ["!=", 2]},
			"name",
		)
		if existing:
			consolidated, unconsolidated = customer_debt(customer, company)
			owed_now = total_owed(consolidated, unconsolidated, precision)
			return {
				"paymentEntry": existing,
				"externalId": external_id,
				# The till prints its receipt from the FIRST answer, so this says
				# plainly that no second payment was taken.
				"reused": True,
				"customer": customer,
				"amount": paid,
				"allocated": [],
				"onAccount": 0.0,
				"owedBefore": owed_now,
				"owedAfter": owed_now,
			}

	consolidated, unconsolidated = customer_debt(customer, company)
	owed = total_owed(consolidated, unconsolidated, precision)

	ok, reason = valid_repayment(paid, owed, precision)
	if not ok:
		# Distinct messages, because they send the cashier to different places:
		# one is a wrong customer, the other a wrong number.
		frappe.throw(
			{
				"amount_not_positive": _("Enter an amount greater than zero."),
				"nothing_owed": _("{0} does not owe anything.").format(customer),
				"over_debt": _("That is more than {0} owes ({1}).").format(
					customer, frappe.format_value(owed, {"fieldtype": "Currency"})
				),
			}[reason]
		)

	# Oldest first: it is what an accountant expects, and it keeps the ageing
	# report honest — paying the newest invoice first would leave an old debt
	# ageing on the report while the customer is in fact paying regularly.
	outstanding = frappe.get_all(
		"Sales Invoice",
		filters={
			"customer": customer,
			"company": company,
			"docstatus": 1,
			"outstanding_amount": [">", 0],
		},
		fields=["name", "outstanding_amount"],
		order_by="posting_date asc, creation asc",
	)
	allocations, unallocated = allocate_repayment(
		paid, [(row.name, row.outstanding_amount) for row in outstanding], precision
	)

	# The mode must be one this till actually offers. Without this a caller
	# could name any Mode of Payment in the system and the money would land in
	# an account this branch has nothing to do with.
	offered = (
		frappe.get_all(
			"POS Payment Method",
			filters={"parent": pos_profile},
			pluck="mode_of_payment",
		)
		if pos_profile
		else []
	)
	if offered and mode_of_payment not in offered:
		frappe.throw(
			_("{0} is not a payment method on this till.").format(mode_of_payment)
		)

	deposit_account = mode_deposit_account(mode_of_payment, company, pos_profile)
	if not deposit_account:
		frappe.throw(
			_(
				"No account is set for {0}. Set its default account on the Mode of Payment, or custom_cash_account on the POS Profile."
			).format(mode_of_payment)
		)

	entry = frappe.new_doc("Payment Entry")
	entry.payment_type = "Receive"
	entry.company = company
	entry.party_type = "Customer"
	entry.party = customer
	entry.mode_of_payment = mode_of_payment
	entry.paid_from = get_party_account("Customer", customer, company)
	entry.paid_to = deposit_account
	entry.paid_amount = paid
	entry.received_amount = paid
	entry.reference_no = pos_profile or "POS"
	entry.reference_date = frappe.utils.nowdate()
	if external_id:
		entry.custom_external_id = external_id
	# Which shift took the money. Without it the Admin Panel's shift page cannot
	# show a repayment at all: a Payment Entry carries no period of its own, and
	# guessing one from its posting date would attribute a payment to whichever
	# shift happened to be open on the same day.
	if pos_opening_entry:
		entry.custom_pos_opening_entry = pos_opening_entry
	if pos_profile:
		entry.cost_center = frappe.db.get_value("POS Profile", pos_profile, "cost_center")
	for name, allocated in allocations:
		entry.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": name,
				"allocated_amount": allocated,
			},
		)
	entry.insert()
	entry.submit()

	after_consolidated, after_unconsolidated = customer_debt(customer, company)
	return {
		"paymentEntry": entry.name,
		"externalId": external_id,
		"reused": False,
		"customer": customer,
		"amount": paid,
		"allocated": [
			{"invoice": name, "amount": allocated} for name, allocated in allocations
		],
		# Money covering debt that has no invoice yet. Reported so the till can
		# say so plainly rather than leaving the cashier to wonder why the
		# customer still shows a balance.
		"onAccount": unallocated,
		"owedBefore": owed,
		"owedAfter": total_owed(after_consolidated, after_unconsolidated, precision),
	}


def _repayment_rows(filters, limit=None, offset=0):
	"""Submitted receipts against customers, newest first.

	Shared by the two listings below so the shape they return cannot drift —
	the Admin Panel renders both through the same row component.

	`docstatus = 1` only: a draft has taken no money and a cancelled one has
	given it back, and showing either as a payment invites a customer to be
	told they have paid when they have not.
	"""
	conditions = ["pe.docstatus = 1", "pe.payment_type = 'Receive'", "pe.party_type = 'Customer'"]
	values = {}
	for field, value in filters.items():
		conditions.append(f"pe.{field} = %({field})s")
		values[field] = value

	limit_clause = ""
	if limit is not None:
		limit_clause = "limit %(limit)s offset %(offset)s"
		values["limit"] = int(limit)
		values["offset"] = int(offset)

	rows = frappe.db.sql(
		f"""
		select pe.name, pe.party as customer, pe.posting_date, pe.creation,
		       pe.paid_amount, pe.mode_of_payment, pe.paid_to,
		       pe.custom_pos_opening_entry as pos_opening_entry,
		       pe.reference_no, pe.owner,
		       pe.paid_from_account_currency as currency,
		       pe.unallocated_amount
		from `tabPayment Entry` pe
		where {" and ".join(conditions)}
		order by pe.creation desc
		{limit_clause}
		""",
		values,
		as_dict=1,
	)

	# Which invoices each payment settled. One query for the page rather than
	# one per row: a customer with a long history would otherwise cost a query
	# per payment, and this list is read on a page a manager opens often.
	names = [r.name for r in rows]
	allocations = {}
	if names:
		for a in frappe.db.sql(
			"""
			select parent, reference_name, allocated_amount
			from `tabPayment Entry Reference`
			where parent in %(names)s and allocated_amount > 0
			""",
			{"names": names},
			as_dict=1,
		):
			allocations.setdefault(a.parent, []).append(
				{"invoice": a.reference_name, "amount": a.allocated_amount}
			)

	return [
		{
			"name": r.name,
			"customer": r.customer,
			"postingDate": str(r.posting_date) if r.posting_date else None,
			"createdAt": str(r.creation) if r.creation else None,
			"amount": r.paid_amount,
			"currency": r.currency,
			"modeOfPayment": r.mode_of_payment,
			"account": r.paid_to,
			"posOpeningEntry": r.pos_opening_entry,
			# A repayment taken at a till carries the profile in reference_no;
			# one keyed in at the desk does not, which is how the Admin Panel
			# tells the two apart without a second field.
			"reference": r.reference_no,
			"recordedBy": r.owner,
			"allocated": allocations.get(r.name, []),
			# Money that could not name an invoice yet — a still-open shift's
			# debt. Reported so a manager is not left wondering why a balance
			# has not moved against a specific invoice.
			"onAccount": r.unallocated_amount or 0.0,
		}
		for r in rows
	]


@frappe.whitelist()
def list_customer_repayments(customer, company=None, limit=20, offset=0):
	"""What this customer has paid off, newest first.

	Every receipt against the customer, not only the ones taken at a till: a
	manager asking "has he paid?" needs the answer, and a payment keyed in at
	the desk settles the same debt as one taken at the counter.
	"""
	if not customer:
		frappe.throw(_("Customer is required."))
	if not frappe.has_permission("Customer", doc=customer):
		frappe.throw(_("Not permitted to read this customer."), frappe.PermissionError)

	filters = {"party": customer}
	if company:
		filters["company"] = company

	total = frappe.db.count(
		"Payment Entry",
		{
			"party_type": "Customer",
			"party": customer,
			"payment_type": "Receive",
			"docstatus": 1,
			**({"company": company} if company else {}),
		},
	)
	return {
		"repayments": _repayment_rows(filters, limit=limit, offset=offset),
		"total": total,
	}


@frappe.whitelist()
def list_shift_repayments(opening_entry):
	"""Debt repaid during one shift.

	Keyed on the opening entry the till stamped onto the Payment Entry. A
	payment with no such stamp belongs to no shift — it was keyed in at the
	desk — and is deliberately absent rather than guessed into the nearest one.
	"""
	if not opening_entry:
		frappe.throw(_("opening_entry is required."))
	if not frappe.has_permission("POS Opening Entry", doc=opening_entry):
		frappe.throw(_("Not permitted to read this shift."), frappe.PermissionError)

	rows = _repayment_rows({"custom_pos_opening_entry": opening_entry})
	return {
		"repayments": rows,
		"total": sum(r["amount"] or 0 for r in rows),
		"count": len(rows),
	}
