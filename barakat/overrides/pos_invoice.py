import frappe
from frappe import _
from frappe.utils import flt, getdate
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice


class BarakatPOSInvoice(POSInvoice):
	def set_outstanding_amount(self):
		# Standard POS Invoice sets outstanding = (rounded_total or grand_total) -
		# paid_amount and ignores write_off_amount entirely. That leaves a fully
		# written-off remainder — e.g. a 0.5 total that rounds to 0, where the
		# customer pays nothing and the 0.5 is written off — stuck as Unpaid.
		# Run ERPNext's standard logic first, then subtract the write-off so the
		# invoice settles to Paid. This only affects invoices that actually carry a
		# write-off (write_off_amount > 0); every other invoice is untouched. It
		# also matches the ledger, which already books the write-off through the
		# inherited Sales Invoice GL posting.
		super().set_outstanding_amount()
		if flt(self.write_off_amount):
			self.outstanding_amount = max(
				0.0, flt(self.outstanding_amount) - flt(self.write_off_amount)
			)

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
