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


@frappe.whitelist()
def record_customer_repayment(
	customer, company, amount, mode_of_payment, pos_profile=None, external_id=None
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

	cash_account = None
	if pos_profile:
		cash_account = frappe.db.get_value(
			"POS Profile", pos_profile, "custom_cash_account"
		)
	if not cash_account:
		frappe.throw(
			_(
				"This till has no cash account. Set custom_cash_account on the POS Profile."
			)
		)

	entry = frappe.new_doc("Payment Entry")
	entry.payment_type = "Receive"
	entry.company = company
	entry.party_type = "Customer"
	entry.party = customer
	entry.mode_of_payment = mode_of_payment
	entry.paid_from = get_party_account("Customer", customer, company)
	entry.paid_to = cash_account
	entry.paid_amount = paid
	entry.received_amount = paid
	entry.reference_no = pos_profile or "POS"
	entry.reference_date = frappe.utils.nowdate()
	if external_id:
		entry.custom_external_id = external_id
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
