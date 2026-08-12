"""The parts built after the first end-to-end pass: QR pairing, unpairing, two devices.

Every case in here is one that was either wrong in a first implementation or reachable
only through a chain nothing else exercises. Two of them are regressions with real
consequences for somebody's pay:

  * a person carrying two devices was clocked out when EITHER dropped off the wifi
  * a pairing ended mid-shift went on counting until midnight, and the shift it left
    open could never be closed by anything

The pairing tests exist because pairing is the fraud surface of this feature — a code
that can be replayed, or claimed by the wrong branch, is somebody standing in a shop
they have never worked in.
"""

from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from barakat.presence import api, pairing, service
from barakat.presence.mode import WIFI

BRANCH = "Map Presence Branch"
PROFILE = "Map Presence Profile"
PHONE = "map0deadbeef1"
TABLET = "map0deadbeef2"


class TestPairingAndMap(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		employees = frappe.get_all("Employee", pluck="name", limit=2)
		cls.employee = employees[0]
		cls.other_employee = employees[1] if len(employees) > 1 else employees[0]
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
		for doctype, name in (("Branch", BRANCH), ("POS Profile", PROFILE)):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _ensure_pos_profile(cls):
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
		frappe.db.delete("Employee Device", {"device_key": ("in", [PHONE, TABLET])})
		# These employees are borrowed from the site, so they can turn up already
		# carrying pairings from real use — and "was that their last device" is decided
		# by exactly that. Left in place, the test passes or fails on the state of
		# somebody else's data.
		frappe.db.delete(
			"Employee Device",
			{"employee": ("in", [cls.employee, cls.other_employee])},
		)
		frappe.db.delete("Presence Pairing Session", {"branch": BRANCH})
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
		frappe.get_doc(
			{
				"doctype": "Presence Settings",
				"custom_company": self.company,
				"mode": WIFI,
				"departure_wait_minutes": 15,
			}
		).insert(ignore_permissions=True)
		frappe.defaults.set_user_default("Company", self.company)
		self.till_name = self._live_till()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ---------------------------------------------------------------- helpers

	def _live_till(self):
		api.request_join(PROFILE, machine_name="DESK-MAP-01")
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		api.approve(till)
		api.request_join(PROFILE)
		frappe.db.set_value(
			"Presence Till",
			till,
			{
				"last_seen": now_datetime(),
				"is_settled": 1,
				"is_blind": 0,
				"local_url": "http://192.168.1.5:7331",
			},
		)
		return till

	def _till(self):
		return frappe.get_doc("Presence Till", self.till_name)

	def _pair(self, device_key, employee=None, valid_from="2020-01-01"):
		frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": self.company,
				"employee": employee or self.employee,
				"device_key": device_key,
				"valid_from": valid_from,
			}
		).insert(ignore_permissions=True)

	def _as_till(self):
		frappe.set_user(frappe.db.get_value("Presence Till", self.till_name, "api_user"))

	def _open_session(self):
		return frappe.db.exists(
			"Presence Session",
			{"employee": self.employee, "branch": BRANCH, "state": "Open"},
		)

	def _age_device(self, device_key, minutes):
		frappe.db.set_value(
			"Presence Live Device",
			f"{BRANCH}::{device_key}",
			"last_seen",
			add_to_date(now_datetime(), minutes=-minutes),
		)

	def _refresh_till(self):
		frappe.db.set_value(
			"Presence Till",
			self.till_name,
			{"last_seen": now_datetime(), "is_settled": 1, "is_blind": 0},
		)

	# ------------------------------------------------------- two devices, one person

	def test_one_of_two_phones_leaving_does_not_end_the_shift(self):
		"""The regression that cost six hours of a nine-hour day.

		A departure used to close the shift without asking whether the person's OTHER
		device was still sitting on the counter.
		"""
		self._pair(PHONE)
		self._pair(TABLET)
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)
		self.assertTrue(self._open_session(), "both phones in, no shift opened")

		self._age_device(PHONE, 40)
		self._refresh_till()
		service.sweep(BRANCH, self.company)

		self.assertTrue(
			self._open_session(), "one phone left and the whole shift was closed"
		)

	def test_the_last_phone_leaving_does_end_the_shift(self):
		self._pair(PHONE)
		self._pair(TABLET)
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)

		self._age_device(PHONE, 40)
		self._age_device(TABLET, 40)
		self._refresh_till()
		service.sweep(BRANCH, self.company)

		self.assertFalse(self._open_session(), "everything left and the shift stayed open")

	def test_a_colleagues_phone_does_not_keep_somebody_at_work(self):
		"""Only the person's OWN devices count. Anything else is a stranger's phone."""
		self._pair(PHONE)
		self._pair(TABLET, employee=self.other_employee)
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)

		self._age_device(PHONE, 40)
		self._refresh_till()
		service.sweep(BRANCH, self.company)

		self.assertFalse(
			self._open_session(),
			"somebody else's phone in the room kept this person clocked in",
		)

	def test_a_second_phone_arriving_does_not_open_a_second_shift(self):
		self._pair(PHONE)
		self._pair(TABLET)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)

		self.assertEqual(
			frappe.db.count(
				"Presence Session",
				{"employee": self.employee, "branch": BRANCH, "state": "Open"},
			),
			1,
		)

	def test_the_shift_records_which_phone_opened_it(self):
		self._pair(PHONE)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		self.assertEqual(
			frappe.db.get_value("Presence Session", self._open_session(), "device_key"),
			PHONE,
		)

	# ------------------------------------------------------------------ unpairing

	def test_unpairing_stops_the_phone_counting_immediately(self):
		"""`valid_to` is a date, so on its own it would keep counting until midnight."""
		self._pair(PHONE)
		row = frappe.db.exists("Employee Device", {"device_key": PHONE})

		self.assertEqual(
			service.employee_for(PHONE, self.company, now_datetime()), self.employee
		)
		pairing.unpair(row)
		self.assertIsNone(
			service.employee_for(
				PHONE, self.company, add_to_date(now_datetime(), seconds=5)
			),
			"the phone was still counting after being unpaired",
		)

	def test_unpairing_leaves_earlier_history_alone(self):
		"""January's session must still resolve to whoever held the phone in January.

		The moment asked about is relative to the unpairing, not a fixed hour: a clock
		time like 06:00 is BEFORE the unpairing on an afternoon run and AFTER it on an
		early-morning one, so the test would pass or fail depending on when it ran.
		"""
		self._pair(PHONE)
		row = frappe.db.exists("Employee Device", {"device_key": PHONE})
		before = add_to_date(now_datetime(), hours=-2)
		pairing.unpair(row)

		self.assertEqual(
			service.employee_for(PHONE, self.company, before), self.employee
		)

	def test_unpairing_the_last_phone_closes_a_shift_nothing_else_could(self):
		"""Otherwise the shift hangs open for good: the device now belongs to nobody,
		so the sweep skips it and never closes anything."""
		self._pair(PHONE)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		self.assertTrue(self._open_session())

		row = frappe.db.exists("Employee Device", {"device_key": PHONE})
		result = pairing.unpair(row)

		self.assertTrue(result["session_closed"])
		self.assertFalse(self._open_session())

	def test_unpairing_one_of_two_phones_leaves_the_shift_running(self):
		"""They may genuinely still be here on the other one."""
		self._pair(PHONE)
		self._pair(TABLET)
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)

		row = frappe.db.exists("Employee Device", {"device_key": PHONE})
		result = pairing.unpair(row)

		self.assertFalse(result["session_closed"])
		self.assertTrue(self._open_session())

	def test_unpairing_twice_is_harmless(self):
		self._pair(PHONE)
		row = frappe.db.exists("Employee Device", {"device_key": PHONE})
		pairing.unpair(row)
		again = pairing.unpair(row)
		self.assertTrue(again["ok"])

	# -------------------------------------------------------------------- QR pairing

	def test_a_code_is_minted_pointing_at_the_branch_till(self):
		started = pairing.start(self.employee, BRANCH)
		self.assertIn("192.168.1.5:7331", started["url"])
		self.assertIn(started["code"], started["url"])

	def test_a_code_cannot_be_used_twice(self):
		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		with self.assertRaises(frappe.ValidationError):
			pairing.claim(started["code"], TABLET)

	def test_an_expired_code_is_refused(self):
		started = pairing.start(self.employee, BRANCH)
		frappe.db.set_value(
			"Presence Pairing Session",
			{"code": started["code"]},
			"expires_at",
			add_to_date(now_datetime(), seconds=-1),
		)
		self._as_till()
		with self.assertRaises(frappe.ValidationError):
			pairing.claim(started["code"], PHONE)

	def test_only_a_till_may_claim(self):
		"""A code is worthless without a till: this is what stops it being used from
		outside the shop, which is the entire security model of QR pairing."""
		started = pairing.start(self.employee, BRANCH)
		with self.assertRaises(frappe.PermissionError):
			pairing.claim(started["code"], PHONE)

	def test_a_claim_creates_the_pairing(self):
		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")
		self.assertEqual(
			service.employee_for(PHONE, self.company, now_datetime()), self.employee
		)

	def test_claiming_a_phone_takes_it_off_whoever_held_it(self):
		"""A phone belongs to one person. The old pairing is CLOSED, never deleted."""
		self._pair(PHONE, employee=self.other_employee)
		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertEqual(
			service.employee_for(PHONE, self.company, now_datetime()), self.employee
		)
		self.assertTrue(
			frappe.db.exists(
				"Employee Device",
				{"device_key": PHONE, "employee": self.other_employee},
			),
			"the previous pairing was deleted instead of closed",
		)

	def test_an_unknown_code_is_refused(self):
		self._as_till()
		with self.assertRaises(frappe.DoesNotExistError):
			pairing.claim("nosuchcode123", PHONE)

	def test_a_second_start_replaces_the_first_code(self):
		"""Two live codes for one person is two ways in where there should be one."""
		first = pairing.start(self.employee, BRANCH)
		pairing.start(self.employee, BRANCH)
		self.assertFalse(
			frappe.db.exists(
				"Presence Pairing Session", {"code": first["code"], "state": "Waiting"}
			)
		)

	def test_status_reports_the_claim(self):
		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")
		self.assertEqual(pairing.status(started["code"])["state"], "Claimed")

	# --------------------------------------------------------------- the timeline

	def test_the_timeline_returns_sessions_and_per_device_spans(self):
		self._pair(PHONE)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)

		today = str(now_datetime().date())
		result = api.timeline(self.employee, today, today)

		self.assertTrue(result["sessions"], "no shift came back")
		self.assertTrue(result["spans"], "no per-device stretch came back")
		self.assertEqual(result["spans"][0]["deviceKey"], PHONE)

	def test_the_timeline_keeps_two_devices_apart(self):
		"""The whole reason spans exist: a session names only the device that opened it."""
		self._pair(PHONE)
		self._pair(TABLET)
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)

		today = str(now_datetime().date())
		result = api.timeline(self.employee, today, today)

		self.assertEqual(len(result["sessions"]), 1, "two phones opened two shifts")
		self.assertEqual(
			{span["deviceKey"] for span in result["spans"]},
			{PHONE, TABLET},
			"the two phones were not reported separately",
		)

	def test_the_timeline_does_not_leak_somebody_elses_phone(self):
		self._pair(PHONE)
		self._pair(TABLET, employee=self.other_employee)
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)

		today = str(now_datetime().date())
		result = api.timeline(self.employee, today, today)

		self.assertNotIn(
			TABLET,
			{span["deviceKey"] for span in result["spans"]},
			"another person's phone was drawn on this person's map",
		)

	def test_a_person_with_no_history_gets_empty_lists_not_an_error(self):
		today = str(now_datetime().date())
		result = api.timeline(self.employee, today, today)
		self.assertEqual(result["sessions"], [])
		self.assertEqual(result["spans"], [])


if __name__ == "__main__":
	frappe.init(site="test")
