import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice

from barakat.cashier_limits import discount_over_cap, has_ad_hoc_line
from barakat.credit_limits import (
	credit_headroom,
	credit_over_limit,
	may_take_credit,
	total_owed,
)
from barakat.overrides.loyalty import align_loyalty_spend, release_redemptions_against
from barakat.rounding import round_half_up


def _rule_names(raw):
	"""The Pricing Rule names inside an item row's `pricing_rules` field.

	The field is a Small Text holding a JSON array (`["PRLE-0001"]`), but be
	liberal about what arrives: a bare string, an already-decoded list, or
	malformed JSON must never raise here — a promotion audit trail is not worth
	rejecting a sale that already happened over.
	"""
	if not raw:
		return []
	if isinstance(raw, (list, tuple)):
		parsed = raw
	else:
		try:
			parsed = json.loads(raw)
		except (TypeError, ValueError):
			return []
		if isinstance(parsed, str):
			parsed = [parsed]
	if not isinstance(parsed, (list, tuple)):
		return []
	return [str(name).strip() for name in parsed if str(name).strip()]


def _profile_limits(pos_profile):
	"""The cashier-limit fields of a POS Profile, as a plain dict.

	Its own function so the tests can stub the lookup instead of creating a
	profile — the rule under test is the comparison, not one site's data.
	"""
	return (
		frappe.db.get_value(
			"POS Profile",
			pos_profile,
			[
				"custom_allow_ad_hoc_item",
				"custom_max_discount_percent",
				"custom_allow_credit_sale",
				"customer",
			],
			as_dict=True,
		)
		or {}
	)


def customer_credit_limit(customer, company):
	"""This customer's credit limit for this company, or 0 for "none".

	Only the customer's OWN row is read. ERPNext's `get_credit_limit` would fall
	back to the Customer Group and then to `Company.credit_limit`, but credit at
	the till is opt-in per customer by design — inheriting a group-wide default
	would hand credit to a walk-in the moment they are added to a group.
	"""
	return frappe.db.get_value(
		"Customer Credit Limit",
		{"parent": customer, "parenttype": "Customer", "company": company},
		"credit_limit",
	)


def customer_debt(customer, company, exclude_invoice=None):
	"""What this customer already owes: consolidated AND unconsolidated.

	Two terms, because ERPNext's own `get_customer_outstanding` reads GL entries
	and a POS Invoice writes none until it is merged at shift close. Counting
	only the GL is what would let one shift breach a limit many times over and
	then fail to consolidate.

	`exclude_invoice` keeps a re-validated invoice from being counted against
	itself — `validate` runs again on amend and on any later save.
	"""
	consolidated = (
		frappe.db.sql(
			"""
			select sum(debit) - sum(credit)
			from `tabGL Entry`
			where party_type = 'Customer' and party = %s
			  and company = %s and is_cancelled = 0
			""",
			(customer, company),
		)[0][0]
		or 0.0
	)

	# Submitted, still unmerged, and therefore invisible above. Raw SQL for the
	# same reason the GL query above uses it: `get_all` refuses a string
	# aggregate ("SQL functions are not allowed as strings in SELECT"), and the
	# only thing wanted here is the sum.
	#
	# `ifnull(consolidated_invoice, '') = ''` rather than `is null`: the field is
	# a Link, and Frappe writes '' rather than NULL often enough that testing
	# only for NULL would count already-merged invoices twice.
	params = [customer, company]
	exclude_clause = ""
	if exclude_invoice:
		# A literal clause, never interpolated user input — the value is bound.
		exclude_clause = "and name != %s"
		params.append(exclude_invoice)

	# Sales add what they left unpaid. Returns SUBTRACT the debt they cancelled,
	# which is the value of the goods coming back less the cash handed over —
	# `abs(total) - abs(paid_amount)`, since a return carries both negated.
	#
	# `outstanding_amount` cannot express the second half: ERPNext computes it as
	# `total - paid if total > paid else 0`, and a return's total is negative, so
	# it always lands on zero. Reading only that column left a customer who
	# returned everything still owing the full amount until shift close, unable
	# to buy on credit again in the meantime.
	unconsolidated = (
		frappe.db.sql(
			f"""
			select sum(
				case when is_return = 1
					then -greatest(
						abs(if(ifnull(rounded_total, 0) <> 0, rounded_total, grand_total))
						- abs(paid_amount),
						0
					)
					else outstanding_amount
				end
			)
			from `tabPOS Invoice`
			where customer = %s and company = %s
			  and docstatus = 1
			  and ifnull(consolidated_invoice, '') = ''
			  {exclude_clause}
			""",
			params,
		)[0][0]
		or 0.0
	)

	return flt(consolidated), flt(unconsolidated)


class BarakatPOSInvoice(POSInvoice):
	def validate(self):
		super().validate()
		self.restore_pos_pricing_rule_details()
		self.validate_cashier_limits()
		self.validate_credit_sale()

	def on_submit(self):
		"""Submit, then make the loyalty ledger's money add up to this bill exactly once.

		The same correction `BarakatSalesInvoice` makes, repeated rather than inherited:
		erpnext's `POSInvoice` extends `SalesInvoice`, but this class extends `POSInvoice`,
		so it does NOT pick up our Sales Invoice override. This is the copy that matters
		in production — `SalesInvoice.on_submit` gates all three of its loyalty paths on
		`not is_consolidated`, so the invoice a shift close produces writes no ledger rows
		at all, and every Barakat sale reaches the ledger as a POS Invoice.

		See `barakat.overrides.loyalty` for the rule and why it exists.
		"""
		super().on_submit()
		align_loyalty_spend(self)

	def delete_loyalty_point_entry(self):
		"""Detach dependent redemptions first, so a return is never refused.

		erpnext throws here when the points this invoice earned have since been spent,
		which strands a customer at the till over an invoice they cannot reach. See
		`barakat.overrides.loyalty.release_redemptions_against` for why detaching is
		both safe and safer than what it replaces.
		"""
		release_redemptions_against(self.doctype, self.name)
		super().delete_loyalty_point_entry()

	def validate_cashier_limits(self):
		"""Enforce the selling profile's two server-visible cashier limits.

		Keyed off the POS Profile, never off a role: the till authenticates as a
		Manager or Branch Supervisor device session with the cashier identified
		only by a PIN, so the submitting user says nothing about who rang the
		sale. See barakat/persona_matrix.py (the Cashier row).
		"""
		if not self.pos_profile:
			# A consolidated or hand-made invoice is not a till. Never judge it
			# by a till's limits.
			return

		limits = _profile_limits(self.pos_profile)

		if not cint(limits.get("custom_allow_ad_hoc_item")):
			if has_ad_hoc_line(row.item_code for row in (self.items or [])):
				frappe.throw(
					_(
						"This till is not allowed to sell custom items. "
						"Remove the typed-in line, or enable "
						"'Allow custom items' on POS Profile {0}."
					).format(self.pos_profile),
					title=_("Custom items not allowed"),
				)

		# A refund's discount mirrors the original sale's — it was already judged
		# when that sale posted. A zero grand total is the rounding-collapse free
		# order push-orders.ts creates by discounting the whole subtotal.
		if cint(self.is_return) or not flt(self.grand_total):
			return

		max_percent = limits.get("custom_max_discount_percent")
		if discount_over_cap(
			self.discount_amount,
			self.total,
			max_percent,
			self.precision("grand_total"),
		):
			frappe.throw(
				_("Discount is above the {0}% limit set on POS Profile {1}.").format(
					flt(max_percent), self.pos_profile
				),
				title=_("Discount too large"),
			)

	def validate_credit_sale(self):
		"""Refuse an unpaid balance the customer is not entitled to.

		THE AUTHORITY for الدفع بالدين. The till checks too, but only this sees
		every till, and only this is reached by an order pushed from a queue that
		was filled while the till was offline — where the balance the cashier saw
		may be hours stale.

		Rejecting here is deliberately the LAST safe moment. ERPNext's own check
		runs at consolidation, on the merged Sales Invoice, and throwing there
		jams the whole shift close for every customer in it. Throwing here fails
		one order, into the retry queue the cashier already understands.

		See docs/superpowers/specs/2026-08-17-pos-credit-sales-design.md.
		"""
		if not self.pos_profile:
			# Not a till: a consolidated or hand-made invoice. Its outstanding is
			# the books' business, not a cashier limit.
			return

		# A credit note reduces debt. Judging it as taking debt on would block
		# exactly the transaction that fixes an over-limit customer.
		if cint(self.is_return):
			return

		precision = self.precision("grand_total")
		invoice_total = flt(self.rounded_total) or flt(self.grand_total)
		debt = flt(invoice_total) - flt(self.paid_amount)
		# Rounded before the comparison so a fraction of an agora left by the
		# rounded-total adjustment is not mistaken for a credit sale.
		debt = round_half_up(debt, precision)
		if debt <= 0:
			return

		limits = _profile_limits(self.pos_profile)

		if not cint(limits.get("custom_allow_credit_sale")):
			frappe.throw(
				_(
					"This till is not allowed to sell on credit. "
					"Take the full amount, or enable 'Allow selling on credit' "
					"on POS Profile {0}."
				).format(self.pos_profile),
				title=_("Credit sales not allowed"),
			)

		# Debt has to attach to a person. The walk-in customer is shared by every
		# anonymous sale, so letting it borrow would pool unrelated strangers'
		# debt onto one record nobody can collect from.
		default_customer = (limits.get("customer") or "").strip()
		if default_customer and self.customer == default_customer:
			frappe.throw(
				_(
					"Choose a customer before selling on credit. "
					"Debt cannot be recorded against the walk-in customer."
				),
				title=_("Customer required"),
			)

		limit = customer_credit_limit(self.customer, self.company)
		if not may_take_credit(limit):
			frappe.throw(
				_(
					"{0} has no credit limit, so this sale must be paid in full. "
					"Set a credit limit for this customer first."
				).format(self.customer),
				title=_("No credit limit"),
			)

		consolidated, unconsolidated = customer_debt(
			self.customer, self.company, exclude_invoice=self.name
		)

		if credit_over_limit(debt, limit, consolidated, unconsolidated, precision):
			headroom = credit_headroom(limit, consolidated, unconsolidated, precision)
			frappe.throw(
				_(
					"{0} may still borrow {1}, but this sale would add {2}. "
					"Their credit limit is {3} and they already owe {4}."
				).format(
					self.customer,
					frappe.format_value(headroom, {"fieldtype": "Currency"}),
					frappe.format_value(debt, {"fieldtype": "Currency"}),
					frappe.format_value(flt(limit), {"fieldtype": "Currency"}),
					frappe.format_value(
						total_owed(consolidated, unconsolidated, precision),
						{"fieldtype": "Currency"},
					),
				),
				title=_("Over the credit limit"),
			)

	def restore_pos_pricing_rule_details(self):
		"""Rebuild the header Pricing Rule Detail table from the item rows.

		The POS applies Pricing Rules itself — it has to, because it sells
		OFFLINE and must price with the rules that were live at the moment of
		sale, not whenever the order happens to sync — so it sends the invoice
		with `ignore_pricing_rule` set and the rates already final.

		ERPNext's `set_missing_item_details` then runs `self.pricing_rules = []`
		unconditionally before applying its own rules, and (with its engine off)
		applies none. So the header table the POS sends is discarded on every
		save, and every POS invoice ends up with an empty one — measured on the
		test bench: 0 rows site-wide, while 11 item rows carried their rule fine.

		The per-item `pricing_rules` field survives that pass, because ERPNext
		only overwrites an item field when it has a replacement value and its
		disabled engine produces none. So the header table is rebuilt from there.

		This is what lets promotion reporting group by rule from the invoice
		header, and it restores the link Frappe uses to refuse deleting a Pricing
		Rule that submitted invoices still reference.
		"""
		# When ERPNext DID apply the rules itself (an invoice made in the desk),
		# it has already filled this table with richer rows than we could
		# reconstruct — margin_type, rate_or_discount. Never touch its work.
		if self.get("pricing_rules"):
			return

		rows = []
		seen = set()
		for item in self.get("items") or []:
			for name in _rule_names(item.get("pricing_rules")):
				if name in seen:
					continue
				seen.add(name)
				# `pricing_rule` is a Link, so a name that no longer exists fails
				# link validation and would reject the whole invoice. An order
				# created offline and pushed after someone deleted its rule must
				# still be recorded — skip the row rather than lose the sale.
				if not frappe.db.exists("Pricing Rule", name):
					continue
				rows.append(
					{
						"pricing_rule": name,
						"item_code": item.get("item_code"),
						"child_docname": item.get("name"),
						"rule_applied": 1,
					}
				)

		for row in rows:
			self.append("pricing_rules", row)

	def validate_change_amount(self):
		"""A refund gives no change, so never derive one from the rounding gap.

		Stock's check is `grand_total < paid_amount`, which is written for a sale
		(customer hands over more than the bill) but is read backwards on a return,
		where both numbers are negative. A refund that pays back less cash than the
		goods are worth — exactly what our whole-shekel rounding produces, e.g. -200
		of goods against -199 of cash — satisfies `-200 < -199` and gets booked as
		₪1 of "change given to the customer".

		Nothing was handed over. The gap is the rounding remainder, and the paired
		refund collects it (200 + 199 = the 399 actually taken). But `get_payments`
		subtracts change_amount from the drawer at shift close, so the cashier is
		told they are ₪1 over on a drawer that balances perfectly.

		ERPNext already guards this in `taxes_and_totals.calculate_change_amount`
		("and not self.doc.is_return") and simply omits it here. Zero the fields
		rather than only skipping the calculation, so a value that arrived on the
		payload cannot survive either.
		"""
		if cint(self.get("is_return")):
			self.change_amount = 0.0
			self.base_change_amount = 0.0
			return
		super().validate_change_amount()

	def validate_pos_opening_entry(self):
		opening_entries = frappe.get_all(
			"POS Opening Entry",
			fields=["name", "period_start_date"],
			filters={"pos_profile": self.pos_profile, "status": "Open"},
			order_by="period_start_date desc",
		)
		if not opening_entries:
			frappe.throw(
				title=_("POS Opening Entry Missing"),
				msg=_("No open POS Opening Entry found for POS Profile {0}.").format(
					frappe.bold(self.pos_profile)
				),
			)
		if len(opening_entries) > 1:
			frappe.throw(
				title=_("Multiple POS Opening Entry"),
				msg=_(
					"POS Profile - {0} has multiple open POS Opening Entries. Please close or cancel the existing entries before proceeding."
				).format(self.pos_profile),
			)
		# Offline-first: only reject if the invoice is dated before the shift
		# opened. The standard today() check breaks offline sync — orders created
		# on Day 1 with internet back on Day 2 still have the correct posting_date
		# and belong to this shift.
		if getdate(self.posting_date) < getdate(opening_entries[0].get("period_start_date")):
			frappe.throw(
				title=_("Invalid Posting Date"),
				msg=_(
					"Invoice posting date cannot be before the POS Opening Entry {0} start date."
				).format(opening_entries[0].get("name")),
			)
