"""Every presence doctype is shop-owned, and none may exist without a company.

The 2026-08-05 Contact / Item Price leak happened because a doctype had nothing for a
Company User Permission to bind to, and because a BLANK marker is visible to everyone.
Both halves are prevented here at the schema level: `custom_company` is a Link to
Company, and it is mandatory, so the blank case cannot be reached at all.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

PRESENCE_DOCTYPES = (
	"Presence Settings",
	"Presence Till",
	"Presence Device",
	"Employee Device",
	"Presence Sighting",
	"Presence Session",
)

TEST_BRANCH = "Presence Test Branch"
TEST_PROFILE = "Presence Test Profile"


class TestPresenceDoctypes(FrappeTestCase):
	def test_every_presence_doctype_exists(self):
		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("DocType", doctype))

	def test_every_presence_doctype_has_a_company_link(self):
		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				field = frappe.get_meta(doctype).get_field("custom_company")
				self.assertIsNotNone(field, f"{doctype} has no custom_company field")
				self.assertEqual(field.fieldtype, "Link")
				self.assertEqual(field.options, "Company")

	def test_company_is_mandatory_on_every_presence_doctype(self):
		"""A blank marker is visible to everyone. Mandatory removes the case."""
		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertTrue(
					frappe.get_meta(doctype).get_field("custom_company").reqd,
					f"{doctype}.custom_company must be reqd",
				)


class TestPresenceTillScope(FrappeTestCase):
	"""A till's branch and company are read, never accepted from the caller.

	A till labelled with the wrong branch puts one person in two places at once and
	makes their attendance nonsense, so neither value may come from whatever the
	watcher or the enrolling POS happens to send.
	"""

	def setUp(self):
		self.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		frappe.db.delete("Presence Till", {"pos_profile": TEST_PROFILE})
		if not frappe.db.exists("Branch", TEST_BRANCH):
			branch = frappe.get_doc(
				{
					"doctype": "Branch",
					"branch": TEST_BRANCH,
					"custom_pos_company": self.company,
					"custom_pos_profiles": [{"pos_profile": TEST_PROFILE}],
				}
			)
			branch.insert(ignore_links=True)

	def test_branch_and_company_are_read_from_the_branch_record(self):
		till = frappe.get_doc(
			{
				"doctype": "Presence Till",
				"pos_profile": TEST_PROFILE,
				"machine_name": "DESK-TEST-01",
			}
		)
		till.insert(ignore_links=True)

		self.assertEqual(till.branch, TEST_BRANCH)
		self.assertEqual(till.custom_company, self.company)

	def test_a_caller_cannot_choose_its_own_branch_or_company(self):
		"""Whatever is sent is overwritten. Scope comes from the record, not the body."""
		till = frappe.get_doc(
			{
				"doctype": "Presence Till",
				"pos_profile": TEST_PROFILE,
				"machine_name": "DESK-TEST-02",
				"branch": "Somewhere Else",
				"custom_company": "Some Other Company",
			}
		)
		till.insert(ignore_links=True)

		self.assertEqual(till.branch, TEST_BRANCH)
		self.assertEqual(till.custom_company, self.company)

	def test_a_profile_belonging_to_no_branch_is_refused(self):
		"""Presence with no branch is meaningless, so this fails loudly at setup."""
		till = frappe.get_doc(
			{
				"doctype": "Presence Till",
				"pos_profile": "Profile With No Branch",
				"machine_name": "DESK-TEST-03",
			}
		)

		with self.assertRaises(frappe.ValidationError):
			till.insert(ignore_links=True)

	def test_a_new_till_starts_pending_not_active(self):
		"""No key is issued until a human approves it."""
		till = frappe.get_doc(
			{
				"doctype": "Presence Till",
				"pos_profile": TEST_PROFILE,
				"machine_name": "DESK-TEST-04",
			}
		)
		till.insert(ignore_links=True)

		self.assertEqual(till.status, "Pending")


class TestEmployeeDevicePairing(FrappeTestCase):
	def setUp(self):
		self.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		self.employees = frappe.get_all("Employee", pluck="name", limit=2)
		frappe.db.delete("Employee Device", {"device_key": ("like", "presence-test-%")})

	def test_a_device_may_belong_to_only_one_employee_at_a_time(self):
		if len(self.employees) < 2:
			self.skipTest("needs two Employee records")

		self._pair(self.employees[0], "presence-test-1")

		with self.assertRaises(frappe.ValidationError):
			self._pair(self.employees[1], "presence-test-1")

	def test_a_closed_pairing_does_not_block_a_new_one(self):
		"""Pairings are closed with a date, never deleted, so history stays readable."""
		if len(self.employees) < 2:
			self.skipTest("needs two Employee records")

		first = self._pair(self.employees[0], "presence-test-2")
		first.valid_to = "2026-08-01"
		first.save()

		second = self._pair(self.employees[1], "presence-test-2")

		self.assertTrue(second.name)

	def test_one_employee_may_hold_several_devices(self):
		"""Phone plus tablet plus an old phone in a drawer. Present if any is seen."""
		self._pair(self.employees[0], "presence-test-3")
		self._pair(self.employees[0], "presence-test-4")

		open_rows = frappe.get_all(
			"Employee Device",
			filters={"employee": self.employees[0], "valid_to": ("is", "not set")},
		)

		self.assertGreaterEqual(len(open_rows), 2)

	def _pair(self, employee, device_key):
		doc = frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": self.company,
				"employee": employee,
				"device_key": device_key,
				"valid_from": "2026-01-01",
			}
		)
		doc.insert()
		return doc
