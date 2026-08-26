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
		self.assertEqual(values["report_window_s"], 10)
		self.assertEqual(values["warmup_s"], 60)
		self.assertEqual(values["sighting_retention_days"], 30)
		self.assertEqual(values["max_devices"], 512)

	def test_a_cleared_report_window_falls_back_rather_than_reporting_every_sweep(self):
		"""Zero here would put the till back to a request every two seconds.

		The window is the only thing standing between a shop and a report per sweep,
		so a cleared field has to mean the default and not "no window at all". The
		till clamps it as well, but a server that hands out zero is a server telling
		every till the wrong thing.
		"""
		self._make(mode="Wifi")
		frappe.db.set_value(
			"Presence Settings",
			{"custom_company": self.company},
			"report_window_s",
			0,
		)

		self.assertEqual(
			settings_for(self.company)["report_window_s"],
			DEFAULTS["report_window_s"],
		)

	def test_a_saved_report_window_overrides_the_default(self):
		self._make(mode="Wifi", report_window_s=30)

		self.assertEqual(settings_for(self.company)["report_window_s"], 30)

	def test_a_saved_value_overrides_the_default(self):
		self._make(mode="Wifi", departure_wait_minutes=8)

		self.assertEqual(settings_for(self.company)["departure_wait_minutes"], 8)

	def test_a_cleared_number_falls_back_to_the_default(self):
		"""A cleared Int reads back as 0, not null, and 0 is never a sane duration.

		Zero here would mean no warm-up at all, so every till reboot would report the
		whole shop as having gone home. A cleared field has to mean "use the default",
		not "use nothing".
		"""
		self._make(mode="Wifi", departure_wait_minutes=8)
		frappe.db.set_value(
			"Presence Settings",
			{"custom_company": self.company},
			"warmup_s",
			0,
		)

		self.assertEqual(settings_for(self.company)["warmup_s"], DEFAULTS["warmup_s"])

	def test_a_cleared_departure_wait_falls_back_rather_than_becoming_instant(self):
		self._make(mode="Wifi")
		frappe.db.set_value(
			"Presence Settings",
			{"custom_company": self.company},
			"departure_wait_minutes",
			0,
		)

		self.assertEqual(
			settings_for(self.company)["departure_wait_minutes"],
			DEFAULTS["departure_wait_minutes"],
		)

	def test_company_is_mandatory(self):
		"""A blank company marker is visible to everyone. Mandatory removes the case.

		The row is named after the company, so the naming step rejects it before the
		mandatory-field check runs. Either way it cannot be saved, which is the point -
		hence the broader ValidationError rather than MandatoryError specifically.
		"""
		doc = frappe.new_doc("Presence Settings")
		doc.mode = "Wifi"

		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def _make(self, **values):
		doc = frappe.new_doc("Presence Settings")
		doc.custom_company = self.company
		doc.update(values)
		doc.insert()
		return doc
