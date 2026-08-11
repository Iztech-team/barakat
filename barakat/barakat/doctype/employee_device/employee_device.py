import frappe
from frappe import _
from frappe.model.document import Document


class EmployeeDevice(Document):
	def validate(self):
		"""A device may belong to only one employee at a time.

		Open rows are those with no `valid_to`. Closing a pairing rather than deleting
		it is what keeps last January's attendance explicable, so this check looks only
		at open rows and never at history - an old closed pairing must not block a new
		owner.
		"""

		if self.valid_to:
			return

		clash = frappe.db.exists(
			"Employee Device",
			{
				"device_key": self.device_key,
				"custom_company": self.custom_company,
				"valid_to": ("is", "not set"),
				"name": ("!=", self.name or ""),
			},
		)
		if not clash:
			return

		owner = frappe.db.get_value("Employee Device", clash, "employee")
		frappe.throw(
			_("This device is already paired to {0}. Close that pairing first.").format(
				owner
			)
		)
