import frappe
from frappe import _
from erpnext.accounts.doctype.pos_opening_entry.pos_opening_entry import POSOpeningEntry


class BarakatPOSOpeningEntry(POSOpeningEntry):
	def validate(self):
		self.validate_pos_profile_and_cashier()
		self.check_device_open_shift()
		self.validate_payment_method_account()
		self.set_status()

	def check_device_open_shift(self):
		if not self.custom_device_id:
			return
		if frappe.db.exists(
			"POS Opening Entry",
			{"custom_device_id": self.custom_device_id, "status": "Open"},
		):
			frappe.throw(
				title=_("Device Already Has Open Shift"),
				msg=_(
					"This device already has an open POS session. Close it before opening a new one."
				),
			)
