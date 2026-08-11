"""Wifi presence is off unless a company deliberately turns it on.

This is the switch that keeps `petromall` and every untouched company completely
unaffected: no endpoint, no jobs, no screens. The default matters more than the
feature, so it is tested first and tested hardest.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.presence.mode import DEFAULTS, is_wifi_mode, settings_for


class TestPresenceMode(FrappeTestCase):
	def setUp(self):
		self.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		frappe.db.delete("Presence Settings", {"custom_company": self.company})

	def test_a_company_with_no_settings_row_is_off(self):
		self.assertFalse(is_wifi_mode(self.company))

	def test_a_company_with_manual_mode_is_off(self):
		self._make(mode="Manual")

		self.assertFalse(is_wifi_mode(self.company))

	def test_a_company_with_wifi_mode_is_on(self):
		self._make(mode="Wifi")

		self.assertTrue(is_wifi_mode(self.company))

	def test_an_unknown_company_is_off(self):
		self.assertFalse(is_wifi_mode("no-such-company"))

	def test_no_company_at_all_is_off(self):
		self.assertFalse(is_wifi_mode(None))
		self.assertFalse(is_wifi_mode(""))

	def test_defaults_are_returned_when_no_row_exists(self):
		values = settings_for(self.company)

		self.assertEqual(values["departure_wait_minutes"], 15)
		self.assertEqual(values["sweep_interval_s"], 2)
		self.assertEqual(values["warmup_s"], 60)
		self.assertEqual(values["sighting_retention_days"], 30)
		self.assertEqual(values["max_devices"], 512)

	def test_a_saved_value_overrides_the_default(self):
		self._make(mode="Wifi", departure_wait_minutes=8)

		self.assertEqual(settings_for(self.company)["departure_wait_minutes"], 8)

	def test_an_unset_value_falls_back_to_the_default(self):
		"""A row that exists must not zero out every number it did not set."""
		self._make(mode="Wifi", departure_wait_minutes=8)
		frappe.db.set_value(
			"Presence Settings",
			{"custom_company": self.company},
			"warmup_s",
			None,
		)

		self.assertEqual(settings_for(self.company)["warmup_s"], DEFAULTS["warmup_s"])

	def test_company_is_mandatory(self):
		"""A blank company marker is visible to everyone. Mandatory removes the case."""
		doc = frappe.new_doc("Presence Settings")
		doc.mode = "Wifi"

		with self.assertRaises(frappe.MandatoryError):
			doc.insert()

	def _make(self, **values):
		doc = frappe.new_doc("Presence Settings")
		doc.custom_company = self.company
		doc.update(values)
		doc.insert()
		return doc
