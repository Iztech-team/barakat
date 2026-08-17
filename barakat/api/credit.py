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
