import frappe
from frappe import _
from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import POSClosingEntry


class BarakatPOSClosingEntry(POSClosingEntry):
	"""Lets any signed-in account close a shift on its till.

	ERPNext binds a shift to one person. `POS Closing Entry.user` is a read-only
	`fetch_from: pos_opening_entry.user`, applied inside `_validate_links()`
	BEFORE `validate()` runs — so the caller cannot set it — and
	`validate_pos_invoices` then rejects every POS Invoice whose `owner` differs
	from it. Its message even prints `self.owner` instead of `self.user`, so the
	rejection reads "isn't created by user Administrator" and points nowhere.

	That model fits a till owned by one cashier. A Barakat till is a shared
	device: the ERPNext login is closer to a device credential than to a person,
	and the person is the PIN, recorded in `custom_opened_by_staff` /
	`custom_closed_by_staff`. When a second account signs in on the same till and
	sells on the open shift, ERPNext makes that shift permanently unclosable.

	So the owner check is dropped and every other check kept. Invoice selection
	is scoped instead by POS Profile and the opening entry's own period — see
	`barakat.api.shift.get_shift_invoices`.
	"""

	def validate_pos_invoices(self):
		invalid_rows = []
		for d in self.pos_invoices:
			invalid_row = {"idx": d.idx}
			pos_invoice = frappe.db.get_values(
				"POS Invoice",
				d.pos_invoice,
				["consolidated_invoice", "pos_profile", "docstatus"],
				as_dict=1,
			)[0]
			if pos_invoice.consolidated_invoice:
				invalid_row.setdefault("msg", []).append(_("POS Invoice is already consolidated"))
				invalid_rows.append(invalid_row)
				continue
			if pos_invoice.pos_profile != self.pos_profile:
				invalid_row.setdefault("msg", []).append(
					_("POS Profile doesn't match {}").format(frappe.bold(self.pos_profile))
				)
			if pos_invoice.docstatus != 1:
				invalid_row.setdefault("msg", []).append(_("POS Invoice is not submitted"))

			if invalid_row.get("msg"):
				invalid_rows.append(invalid_row)

		if not invalid_rows:
			return

		error_list = []
		for row in invalid_rows:
			for msg in row.get("msg"):
				error_list.append(_("Row #{}: {}").format(row.get("idx"), msg))

		frappe.throw(error_list, title=_("Invalid POS Invoices"), as_list=True)
