import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice

from barakat.cashier_limits import discount_over_cap, has_ad_hoc_line
from barakat.overrides.loyalty import align_loyalty_spend


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
			["custom_allow_ad_hoc_item", "custom_max_discount_percent"],
			as_dict=True,
		)
		or {}
	)


class BarakatPOSInvoice(POSInvoice):
	def validate(self):
		super().validate()
		self.restore_pos_pricing_rule_details()
		self.validate_cashier_limits()

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
