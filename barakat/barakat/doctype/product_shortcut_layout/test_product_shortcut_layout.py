import frappe
from frappe.tests.utils import FrappeTestCase


def _layout(name, item_codes):
	return frappe.get_doc(
		{
			"doctype": "Product Shortcut Layout",
			"layout_name": name,
			"company": frappe.defaults.get_user_default("Company"),
			"items": [{"item_code": c} for c in item_codes],
		}
	)


class TestProductShortcutLayout(FrappeTestCase):
	def test_rejects_more_than_100_items(self):
		doc = _layout("Too Many", [f"ITEM-{i}" for i in range(101)])
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_rejects_duplicate_item(self):
		doc = _layout("Dupes", ["ITEM-1", "ITEM-2", "ITEM-1"])
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_accepts_100_distinct_items_and_keeps_order(self):
		codes = [f"ITEM-{i}" for i in range(100)]
		doc = _layout("Full", codes)
		doc.validate()
		self.assertEqual([r.item_code for r in doc.items], codes)
