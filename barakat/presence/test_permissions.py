"""Who can see and change presence data.

Three failures this guards, and all three have happened in this codebase before:

  - The OWNER blind spot. Owner accounts hold System Manager and no persona, and hit
    ERPNext under their own native roles. A doctype permed only through a Barakat
    persona role renders in the admin panel and 403s for the owner. Here that would
    mean a shop owner cannot see his own staff's attendance.
  - Reach that was never granted. A Cashier must not read presence at all.
  - Pairing reaching too far. Pairing a phone to a person is the one action here that
    can be used to commit fraud, so it is Manager-only and HR must not have it.

Read straight off the DocPerm tables rather than by calling the API. The desktop POS
wraps every sync in try/catch and falls back to defaults, and the watcher has exactly
that shape - a missing permission would look like "not configured" forever rather than
raising anything. Auditing the table is the only way this fails loudly.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.permissions import bundle_for

PRESENCE_DOCTYPES = (
	"Presence Settings",
	"Presence Till",
	"Presence Device",
	"Employee Device",
	"Presence Sighting",
	"Presence Session",
)


def roles_with(doctype, ptype):
	"""Every role holding `ptype` on `doctype` at permlevel 0.

	`get_all` is deliberate here: this audits the permission tables themselves, which
	is the one case where ignoring permissions is the correct behaviour rather than
	the bug it usually is.
	"""

	custom = frappe.get_all(
		"Custom DocPerm",
		filters={"parent": doctype, ptype: 1, "permlevel": 0},
		pluck="role",
	)
	if custom:
		return set(custom)

	return set(
		frappe.get_all(
			"DocPerm",
			filters={"parent": doctype, ptype: 1, "permlevel": 0},
			pluck="role",
		)
	)


class TestOwnerCanReachPresence(FrappeTestCase):
	def test_system_manager_has_full_access_to_every_presence_doctype(self):
		"""The owner path. System Manager is what an owner actually holds."""
		for doctype in PRESENCE_DOCTYPES:
			for ptype in ("read", "write", "create", "delete"):
				with self.subTest(doctype=doctype, ptype=ptype):
					self.assertIn(
						"System Manager",
						roles_with(doctype, ptype),
						f"System Manager has no {ptype} on {doctype} — owners will 403",
					)


class TestPersonaReach(FrappeTestCase):
	def test_a_cashier_cannot_read_any_presence_data(self):
		cashier = set(bundle_for("Cashier"))

		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertEqual(
					roles_with(doctype, "read") & cashier,
					set(),
					f"Cashier can read {doctype}",
				)

	def test_a_manager_can_pair_a_device(self):
		manager = set(bundle_for("Manager"))

		self.assertTrue(
			roles_with("Employee Device", "write") & manager,
			"Manager cannot pair a device",
		)

	def test_hr_can_see_pairings_but_not_create_them(self):
		"""Pairing is the fraud surface, so it sits on the narrower staff:write gate."""
		hr = set(bundle_for("HR"))

		self.assertTrue(
			roles_with("Employee Device", "read") & hr,
			"HR cannot see pairings",
		)
		self.assertEqual(
			roles_with("Employee Device", "write") & hr,
			set(),
			"HR can pair a device — pairing must be Manager-only",
		)

	def test_a_manager_can_turn_wifi_presence_on(self):
		manager = set(bundle_for("Manager"))

		self.assertTrue(
			roles_with("Presence Settings", "write") & manager,
			"Manager cannot change the attendance mode",
		)
