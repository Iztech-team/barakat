"""On-bench tests for the company abbreviation availability check. Run on a site:

	bench --site <site> run-tests --module barakat.api.test_company

Not runnable on the Windows dev box (imports `frappe`).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.api.company import is_abbreviation_available

# An abbreviation no real company would hold, used for the "free" assertions.
FREE_ABBR = "ZZQX9"
# The abbreviation the fixture company takes, used for the "taken" assertions.
FIXTURE_ABBR = "ZQTC"


class TestIsAbbreviationAvailable(FrappeTestCase):
	def setUp(self):
		self.existing_abbr = frappe.db.get_value("Company", {}, "abbr")
		self.assertTrue(self.existing_abbr, "test site has no Company to check against")

	def test_reports_an_unused_abbreviation_as_available(self):
		self.assertEqual(is_abbreviation_available(FREE_ABBR), {"available": True})

	def test_reports_a_used_abbreviation_as_unavailable(self):
		self.assertEqual(is_abbreviation_available(self.existing_abbr), {"available": False})

	def test_trims_surrounding_whitespace(self):
		# ERPNext's validate_abbr does `self.abbr = self.abbr.strip()` before its
		# uniqueness check, so this must agree or the two disagree at the margin.
		self.assertEqual(
			is_abbreviation_available(f"  {self.existing_abbr}  "), {"available": False}
		)

	def test_reports_an_empty_abbreviation_as_unavailable(self):
		# ERPNext rejects an empty abbr too ("Abbreviation is mandatory"). A stray
		# call should get a boring answer rather than a traceback.
		self.assertEqual(is_abbreviation_available(""), {"available": False})
		self.assertEqual(is_abbreviation_available("   "), {"available": False})
		self.assertEqual(is_abbreviation_available(None), {"available": False})

	def test_sees_a_company_the_caller_has_no_permission_for(self):
		"""The whole reason this method exists in the Frappe app.

		With a `Company` User Permission that excludes the owning company, a
		permission-scoped read returns nothing and would report the abbreviation
		free — then ERPNext's own unscoped SQL rejects the create anyway. This
		test fails the moment someone "simplifies" the lookup into a scoped query.
		"""
		user = "test-abbr-scope@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Abbr Scope",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

		other = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "Abbr Scope Test Co",
				"abbr": FIXTURE_ABBR,
				"default_currency": frappe.db.get_value("Company", {}, "default_currency") or "ILS",
				"country": frappe.db.get_value("Company", {}, "country") or "United States",
			}
		).insert(ignore_permissions=True)

		# Restrict the user to a DIFFERENT company, so a scoped read is blind to `other`.
		mine = frappe.db.get_value("Company", {"name": ("!=", other.name)}, "name")
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "Company",
				"for_value": mine,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(user)
		try:
			# A permission-scoped read cannot see it …
			self.assertEqual(frappe.get_all("Company", filters={"abbr": FIXTURE_ABBR}), [])
			# … but the check must.
			self.assertEqual(is_abbreviation_available(FIXTURE_ABBR), {"available": False})
		finally:
			frappe.set_user("Administrator")
