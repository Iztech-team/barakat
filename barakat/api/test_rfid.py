import json
import frappe
from frappe.tests.utils import FrappeTestCase
from barakat.api.rfid import commission_units


class TestCommissionUnits(FrappeTestCase):
	def setUp(self):
		# A serialized item + a warehouse must exist in the test site.
		self.item_code = "RFID-TEST-ITEM"
		self.warehouse = frappe.db.get_value("Warehouse", {"is_group": 0}, "name")
		if not frappe.db.exists("Item", self.item_code):
			frappe.get_doc({
				"doctype": "Item", "item_code": self.item_code,
				"item_name": self.item_code, "item_group": "All Item Groups",
				"stock_uom": "Nos", "is_stock_item": 1, "has_serial_no": 1,
				"serial_no_series": "",
			}).insert(ignore_permissions=True)

	def test_creates_serials_and_is_idempotent(self):
		epcs = ["BE3A0001AAAA0001", "BE3A0001AAAA0002"]
		r1 = commission_units(self.item_code, self.warehouse, json.dumps(epcs))
		self.assertEqual(sorted(r1["created"]), sorted(epcs))
		self.assertEqual(r1["already"], [])
		for epc in epcs:
			self.assertTrue(frappe.db.exists("Serial No", epc))

		# Re-sending the same EPCs creates nothing new.
		r2 = commission_units(self.item_code, self.warehouse, json.dumps(epcs))
		self.assertEqual(r2["created"], [])
		self.assertEqual(sorted(r2["already"]), sorted(epcs))
		self.assertIsNone(r2["stock_entry"])

	def test_rejects_non_serialized_item(self):
		with self.assertRaises(frappe.ValidationError):
			commission_units("Non-Serial-X", self.warehouse, json.dumps(["BE00"]))
