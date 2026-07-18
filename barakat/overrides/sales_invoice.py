import frappe
from frappe.utils import cint, cstr, flt

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice


class BarakatSalesInvoice(SalesInvoice):
	"""Book POS loyalty redemptions to the general ledger.

	ERPNext gates `make_loyalty_point_redemption_gle` on `not is_consolidated`, but a POS
	Invoice produces no GL of its own — its ledger is only written when the shift closes and
	the invoices are merged into a *consolidated* Sales Invoice. The redemption therefore
	falls through the crack in both states and is never booked anywhere: the value paid with
	points is left sitting in Debtors as a receivable nobody will ever collect, and on the
	return side the same gap is dumped into Write Off.

	Two surgical overrides fix that, and both are strict no-ops for anything that is not a
	loyalty-bearing consolidated invoice:

	* the sale books the stock redemption entry (DR redemption account / CR Debtors);
	* the return's balancing write-off is redirected to the same redemption account, so it
	  reverses the sale's cost instead of landing in Write Off.

	Upstream issue: the `is_consolidated` gate is still present in erpnext (see
	frappe/erpnext#41514, #41036, #31509) — nothing in core books POS loyalty redemptions.
	"""

	# ── sale ──────────────────────────────────────────────────────────────────────

	def make_loyalty_point_redemption_gle(self, gl_entries):
		if self._barakat_books_consolidated_redemption():
			self._barakat_append_redemption_gle(gl_entries)
			return
		super().make_loyalty_point_redemption_gle(gl_entries)

	def _barakat_books_consolidated_redemption(self):
		"""True only for a consolidated SALE that actually carries a redemption.

		Every condition matters: a non-consolidated invoice is already handled by stock, a
		return is handled by the write-off redirect below, and without an account resolved
		from the Loyalty Program we must not post at all rather than post to nothing.
		"""
		return bool(
			cint(self.get("is_consolidated"))
			and not cint(self.get("is_return"))
			and cint(self.get("redeem_loyalty_points"))
			and self.get("loyalty_points")
			and self.get("loyalty_program")
			and flt(self.get("loyalty_amount"))
			and self.get("loyalty_redemption_account")
		)

	def _barakat_append_redemption_gle(self, gl_entries):
		"""The two stock redemption lines, without the `is_consolidated` condition.

		Copied from erpnext's `make_loyalty_point_redemption_gle` so this stays correct even
		as the gate we are bypassing changes. Re-check on a major ERPNext upgrade.
		"""
		gl_entries.append(
			self.get_gl_dict(
				{
					"account": self.debit_to,
					"party_type": "Customer",
					"party": self.customer,
					"against": "Expense account - "
					+ cstr(self.loyalty_redemption_account)
					+ " for the Loyalty Program",
					"credit": self.loyalty_amount,
					"credit_in_transaction_currency": self.loyalty_amount,
					"against_voucher": self.return_against if cint(self.is_return) else self.name,
					"against_voucher_type": self.doctype,
					"cost_center": self.cost_center,
				},
				item=self,
			)
		)
		gl_entries.append(
			self.get_gl_dict(
				{
					"account": self.loyalty_redemption_account,
					"cost_center": self.cost_center or self.loyalty_redemption_cost_center,
					"against": self.customer,
					"debit": self.loyalty_amount,
					"debit_in_transaction_currency": self.loyalty_amount,
					"remark": "Loyalty Points redeemed by the customer",
				},
				item=self,
			)
		)

	# ── return ────────────────────────────────────────────────────────────────────

	def make_write_off_gl_entry(self, gl_entries):
		"""Send a loyalty return's balancing entry to the redemption account.

		A consolidated credit note for a POS return is short by exactly the value the
		customer originally paid in points: the goods come back at full value but only the
		cash is refunded. ERPNext balances that gap with a write-off, which is meaningless —
		nothing was written off, the difference is the loyalty tender being reversed.

		Rather than add entries or fiddle with amounts, redirect the account: the write-off
		is already the right amount and the right sign (a credit on a credit note), so
		booking it to the redemption account reverses the debit the sale made. Over a sale
		and its full return the redemption account nets to zero.
		"""
		account = self._barakat_loyalty_reversal_account()
		if not account:
			super().make_write_off_gl_entry(gl_entries)
			return

		original_account = self.write_off_account
		self.write_off_account = account
		try:
			super().make_write_off_gl_entry(gl_entries)
		finally:
			# Never leave the swapped account on the document — the redirect is a GL-time
			# concern only, and later code (or a re-submit) must see the real field.
			self.write_off_account = original_account

	def _barakat_loyalty_reversal_account(self):
		"""The redemption account to reverse into, or None to leave stock behavior alone.

		Only a consolidated return, against an invoice that actually redeemed points, with a
		non-zero gap to reverse. A plain write-off on any other invoice is untouched.
		"""
		if not (
			cint(self.get("is_consolidated"))
			and cint(self.get("is_return"))
			and self.get("return_against")
			and flt(self.get("write_off_amount"))
		):
			return None

		account = self.get("loyalty_redemption_account")
		if not account:
			original = frappe.db.get_value(
				"Sales Invoice",
				self.return_against,
				["loyalty_redemption_account", "redeem_loyalty_points"],
				as_dict=True,
			)
			if not original or not cint(original.redeem_loyalty_points):
				return None
			account = original.loyalty_redemption_account

		return account or None
