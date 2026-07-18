import frappe
from frappe import _
from frappe.utils import cint, getdate
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice


class BarakatPOSInvoice(POSInvoice):
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
