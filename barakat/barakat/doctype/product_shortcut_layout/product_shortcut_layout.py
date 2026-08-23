import frappe
from frappe import _
from frappe.model.document import Document

# 4 pages of 5x5 on the till. The cap is enforced here as well as in the proxy
# because the till renders whatever the doctype holds — a 101st row would be
# invisible to the cashier and impossible to explain.
MAX_SHORTCUTS = 100


class ProductShortcutLayout(Document):
	def validate(self):
		self.validate_count()
		self.validate_no_duplicates()

	def validate_count(self):
		if len(self.items or []) > MAX_SHORTCUTS:
			frappe.throw(
				_("A shortcut layout can hold at most {0} items.").format(MAX_SHORTCUTS),
				title=_("Too many items"),
			)

	def validate_no_duplicates(self):
		# Two tiles for the same item is always a mistake: the cashier cannot tell
		# them apart, and the second one burns a slot out of a budget of 100.
		seen = set()
		for row in self.items or []:
			if row.item_code in seen:
				frappe.throw(
					_("Item {0} appears more than once in this layout.").format(row.item_code),
					title=_("Duplicate item"),
				)
			seen.add(row.item_code)
