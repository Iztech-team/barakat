"""A till joins, gets approved, reports a phone, and somebody's shift opens and closes.

The whole chain in one file. Every other test in this feature checks one layer; this one
proves the layers are actually connected, which is the failure no unit test can see.
"""

from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from barakat.presence import api, service
from barakat.presence.mode import MANUAL, WIFI

BRANCH = "E2E Presence Branch"
PROFILE = "E2E Presence Profile"
PHONE = "e2edeadbeef01"
STRANGER = "e2ecafef00d99"


class TestPresenceEndToEnd(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.employee = frappe.get_all("Employee", pluck="name", limit=1)[0]
		cls._ensure_pos_profile()

		if not frappe.db.exists("Branch", BRANCH):
			frappe.get_doc(
				{
					"doctype": "Branch",
					"branch": BRANCH,
					"custom_pos_company": cls.company,
					"custom_pos_profiles": [{"pos_profile": PROFILE}],
				}
			).insert(ignore_links=True, ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._wipe()
		if frappe.db.exists("Branch", BRANCH):
			frappe.delete_doc("Branch", BRANCH, force=True, ignore_permissions=True)
		if frappe.db.exists("POS Profile", PROFILE):
			frappe.delete_doc("POS Profile", PROFILE, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _ensure_pos_profile(cls):
		"""A POS Profile that exists, and nothing more.

		A real one needs twelve mandatory fields including several Barakat accounts,
		plus a payment method and a warehouse. Presence needs none of that - it only
		needs the profile to EXIST, because `Presence Till` links to it and reads its
		branch from the Branch record. So this is inserted with validation bypassed:
		building a working till profile here would be fixture work that tests ERPNext
		rather than presence.

		Note what is NOT bypassed: `Presence Till` still resolves its own branch and
		company for real, which is the part under test.
		"""
		if frappe.db.exists("POS Profile", PROFILE):
			return
		doc = frappe.get_doc({"doctype": "POS Profile", "company": cls.company})
		doc.name = PROFILE
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)

	@classmethod
	def _wipe(cls):
		for doctype in (
			"Presence Sighting",
			"Presence Live Device",
			"Presence Session",
		):
			frappe.db.delete(doctype, {"branch": BRANCH})
		frappe.db.delete("Employee Device", {"device_key": ("in", [PHONE, STRANGER])})
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		if till:
			user = frappe.db.get_value("Presence Till", till, "api_user")
			frappe.delete_doc("Presence Till", till, force=True, ignore_permissions=True)
			if user and frappe.db.exists("User", user):
				frappe.delete_doc("User", user, force=True, ignore_permissions=True)
		frappe.db.delete("Presence Settings", {"custom_company": cls.company})

	def setUp(self):
		frappe.set_user("Administrator")
		self._wipe()
		self._set_mode(WIFI)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ---------------------------------------------------------------- helpers

	def _set_mode(self, mode):
		frappe.get_doc(
			{"doctype": "Presence Settings", "custom_company": self.company, "mode": mode}
		).insert(ignore_permissions=True)

	def _join(self):
		return api.request_join(PROFILE, machine_name="DESK-E2E-01")

	def _approve(self):
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		api.approve(till)
		return till

	def _pair_phone(self):
		frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": self.company,
				"employee": self.employee,
				"device_key": PHONE,
				"valid_from": "2020-01-01",
			}
		).insert(ignore_permissions=True)

	def _as_till(self, till):
		"""Act as the watcher's own account, which holds no permissions at all."""
		frappe.set_user(frappe.db.get_value("Presence Till", till, "api_user"))

	def _open_session(self):
		return frappe.db.exists(
			"Presence Session",
			{"employee": self.employee, "branch": BRANCH, "state": "Open"},
		)

	# ---------------------------------------------------------------- enrolment

	def test_a_new_till_is_pending_and_gets_no_key(self):
		result = self._join()

		self.assertEqual(result["status"], "pending")
		self.assertNotIn("api_secret", result)

	def test_a_till_gets_its_key_only_after_a_human_approves(self):
		self._join()
		self._approve()

		result = self._join()

		self.assertEqual(result["status"], "approved")
		self.assertTrue(result["api_key"])
		self.assertTrue(result["api_secret"])

	def test_the_key_is_handed_over_exactly_once(self):
		self._join()
		self._approve()
		self._join()

		again = self._join()

		self.assertEqual(again["status"], "active")
		self.assertNotIn("api_secret", again)

	def test_the_watcher_account_holds_no_role_that_grants_anything(self):
		"""A stolen key must be worth nothing. Its account can reach no data at all."""
		self._join()
		till = self._approve()
		self._join()
		user = frappe.db.get_value("Presence Till", till, "api_user")

		self._as_till(till)
		try:
			for doctype in ("Employee", "Customer", "Presence Session", "Presence Device"):
				with self.subTest(doctype=doctype):
					self.assertFalse(
						frappe.has_permission(doctype, "read"),
						f"the watcher account can read {doctype}",
					)
		finally:
			frappe.set_user("Administrator")

		self.assertNotIn(
			"System Manager", {r.role for r in frappe.get_doc("User", user).roles}
		)

	def test_a_company_in_manual_mode_issues_nothing(self):
		frappe.db.delete("Presence Settings", {"custom_company": self.company})
		self._set_mode(MANUAL)

		self.assertEqual(self._join()["status"], "off")

	# ---------------------------------------------------------------- reporting

	def test_a_reported_phone_opens_a_shift_for_the_person_it_belongs_to(self):
		self._pair_phone()
		self._join()
		till = self._approve()
		self._join()

		self._as_till(till)
		try:
			result = api.report(devices=[{"id": PHONE}], seq=1)
		finally:
			frappe.set_user("Administrator")

		self.assertTrue(result["ok"])
		self.assertTrue(self._open_session(), "no shift was opened")
		self.assertTrue(
			frappe.db.exists("Presence Live Device", f"{BRANCH}::{PHONE}"),
			"the branch's live view was not updated",
		)
		self.assertTrue(
			frappe.db.exists(
				"Presence Sighting", {"device_key": PHONE, "event": "appeared"}
			),
			"no sighting was recorded",
		)

	def test_an_unpaired_phone_is_recorded_but_belongs_to_nobody(self):
		"""A customer walking past is a device, not a member of staff."""
		self._join()
		till = self._approve()
		self._join()

		self._as_till(till)
		try:
			api.report(devices=[{"id": STRANGER}], seq=1)
		finally:
			frappe.set_user("Administrator")

		self.assertTrue(frappe.db.exists("Presence Live Device", f"{BRANCH}::{STRANGER}"))
		self.assertFalse(self._open_session())

	def test_seeing_the_same_phone_again_does_not_open_a_second_shift(self):
		self._pair_phone()
		self._join()
		till = self._approve()
		self._join()

		self._as_till(till)
		try:
			api.report(devices=[{"id": PHONE}], seq=1)
			api.report(devices=[{"id": PHONE}], seq=2)
			api.report(devices=[{"id": PHONE}], seq=3)
		finally:
			frappe.set_user("Administrator")

		self.assertEqual(
			len(
				frappe.get_all(
					"Presence Session", filters={"employee": self.employee, "branch": BRANCH}
				)
			),
			1,
		)

	def test_a_replayed_report_is_refused(self):
		self._join()
		till = self._approve()
		self._join()

		self._as_till(till)
		try:
			api.report(devices=[{"id": STRANGER}], seq=5)
			with self.assertRaises(Exception):
				api.report(devices=[{"id": STRANGER}], seq=5)
		finally:
			frappe.set_user("Administrator")

	def test_a_suspended_till_is_refused(self):
		self._join()
		till = self._approve()
		self._join()
		api.suspend(till)

		self._as_till(till)
		try:
			with self.assertRaises(frappe.PermissionError):
				api.report(devices=[{"id": STRANGER}], seq=1)
		finally:
			frappe.set_user("Administrator")

	def test_a_stranger_with_no_till_is_refused(self):
		"""Being authenticated is not the same as being a till."""
		with self.assertRaises(frappe.PermissionError):
			api.report(devices=[{"id": STRANGER}], seq=1)

	# ---------------------------------------------------------------- departures

	def test_a_phone_that_stops_being_seen_closes_the_shift(self):
		self._pair_phone()
		self._join()
		till = self._approve()
		self._join()

		self._as_till(till)
		try:
			api.report(devices=[{"id": PHONE}], seq=1)
		finally:
			frappe.set_user("Administrator")

		session = self._open_session()
		self.assertTrue(session)

		# Wind the clock back rather than waiting fifteen real minutes. The sighting is
		# what ages; the sweep is what notices.
		vanished = add_to_date(now_datetime(), minutes=-20)
		frappe.db.set_value(
			"Presence Live Device", f"{BRANCH}::{PHONE}", "last_seen", vanished
		)
		frappe.db.set_value(
			"Presence Till", till, "last_seen", now_datetime(), update_modified=False
		)

		departures = service.sweep(BRANCH, self.company)

		self.assertEqual(len(departures), 1)
		self.assertFalse(self._open_session(), "the shift is still open")

		closed = frappe.get_doc("Presence Session", session)
		self.assertEqual(closed.state, "Closed")
		# The person left when their phone vanished, not when the timer ran out.
		self.assertLess(
			abs((closed.out_time - vanished).total_seconds()), 5, "wrong leaving time"
		)

	def test_a_phone_that_comes_back_in_time_keeps_the_shift_open(self):
		"""The pocket-sleep case, end to end."""
		self._pair_phone()
		self._join()
		till = self._approve()
		self._join()

		self._as_till(till)
		try:
			api.report(devices=[{"id": PHONE}], seq=1)
		finally:
			frappe.set_user("Administrator")

		frappe.db.set_value(
			"Presence Live Device",
			f"{BRANCH}::{PHONE}",
			"last_seen",
			add_to_date(now_datetime(), minutes=-5),
		)
		frappe.db.set_value(
			"Presence Till", till, "last_seen", now_datetime(), update_modified=False
		)

		departures = service.sweep(BRANCH, self.company)

		self.assertEqual(departures, [])
		self.assertTrue(self._open_session(), "the shift was closed too early")

	def test_a_branch_nobody_can_see_never_sends_anyone_home(self):
		"""Unreachable is not empty. This is the failure that would go unnoticed."""
		self._pair_phone()
		self._join()
		till = self._approve()
		self._join()

		self._as_till(till)
		try:
			api.report(devices=[{"id": PHONE}], seq=1)
		finally:
			frappe.set_user("Administrator")

		# The phone aged out AND the till went silent - a power cut, not a quiet shop.
		frappe.db.set_value(
			"Presence Live Device",
			f"{BRANCH}::{PHONE}",
			"last_seen",
			add_to_date(now_datetime(), minutes=-60),
		)
		frappe.db.set_value(
			"Presence Till",
			till,
			"last_seen",
			add_to_date(now_datetime(), minutes=-60),
			update_modified=False,
		)

		departures = service.sweep(BRANCH, self.company)

		self.assertEqual(departures, [])
		self.assertTrue(self._open_session(), "a dead branch sent everybody home")

	# ------------------------------------------------- a till that lost its key

	def test_a_till_that_lost_its_key_says_so_instead_of_going_quiet(self):
		"""The failure that used to be indistinguishable from a network fault.

		A reimaged PC comes back with an empty store and asks to join. The server has
		already issued this till a key and cannot issue it again — Frappe keeps only a
		hash — so it is told "active" and it reports nothing, for ever. In the Admin
		Panel that looked exactly like a branch whose router was down, and the fix
		(reissue) is nothing like the fix somebody would go looking for.
		"""
		self._join()
		till = self._approve()
		first = self._join()
		self.assertEqual(first["status"], "approved")
		self.assertIsNone(
			frappe.db.get_value("Presence Till", till, "asked_again_at"),
			"collecting a key for the first time is not a complaint",
		)

		# The PC is reimaged: the app has no credentials and asks again.
		again = self._join()
		self.assertEqual(again["status"], "active")
		self.assertNotIn("api_key", again, "a key is never handed out twice")
		self.assertIsNotNone(
			frappe.db.get_value("Presence Till", till, "asked_again_at"),
			"the asking is the only evidence the key is gone — it must be recorded",
		)

	def test_reissue_actually_kills_the_old_key(self):
		"""It said so in its own docstring and did not do it.

		Clearing `key_issued_at` only makes the SERVER forget it handed one out. The
		credential kept working, because nothing disabled the account — so a till
		reissued after being stolen or reimaged carried on reporting on the old key,
		and the panel sat on "Collecting its key" for ever, since the till had no reason
		to ask for a replacement it did not need.
		"""
		self._join()
		till = self._approve()
		self._join()
		user = frappe.db.get_value("Presence Till", till, "api_user")
		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 1)

		api.reissue(till)

		self.assertEqual(
			frappe.db.get_value("User", user, "enabled"),
			0,
			"the old key must stop working the moment reissue is pressed",
		)

	def test_the_till_collects_a_working_key_after_a_reissue(self):
		"""Revoking must not strand the till — asking again has to bring it back."""
		self._join()
		till = self._approve()
		self._join()
		api.reissue(till)

		again = self._join()
		self.assertEqual(again["status"], "approved")
		self.assertTrue(again["api_secret"], "a fresh secret must be handed over")
		self.assertEqual(
			frappe.db.get_value("User", frappe.db.get_value("Presence Till", till, "api_user"), "enabled"),
			1,
			"collecting the new key must re-enable the account",
		)
		self.assertIsNotNone(frappe.db.get_value("Presence Till", till, "key_issued_at"))

	def test_a_pending_till_asking_repeatedly_is_not_a_lost_key(self):
		"""Waiting for a manager is normal and must not look like a fault."""
		self._join()
		self._join()
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		self.assertIsNone(
			frappe.db.get_value("Presence Till", till, "asked_again_at")
		)

	def test_reissuing_answers_the_complaint(self):
		"""The badge must clear when the manager does the thing it asked for."""
		self._join()
		till = self._approve()
		self._join()
		self._join()
		self.assertIsNotNone(frappe.db.get_value("Presence Till", till, "asked_again_at"))

		api.reissue(till)
		self.assertIsNone(
			frappe.db.get_value("Presence Till", till, "asked_again_at"),
			"a manager who has just reissued must not still be asked to reissue",
		)
		self.assertIsNone(frappe.db.get_value("Presence Till", till, "key_issued_at"))

		# And the till collects the new key on its next ask, which is the whole point.
		self.assertEqual(self._join()["status"], "approved")

	def test_reactivating_a_suspended_till_also_clears_it(self):
		self._join()
		till = self._approve()
		self._join()
		self._join()
		api.suspend(till)
		api.reactivate(till)
		self.assertIsNone(frappe.db.get_value("Presence Till", till, "asked_again_at"))
		self.assertEqual(self._join()["status"], "approved")
