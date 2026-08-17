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

from datetime import datetime, time as dt_time, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from barakat.presence import api, pairing, service
from barakat.presence.mode import WIFI

BRANCH = "Map Presence Branch"
PROFILE = "Map Presence Profile"
# A second till at the same branch, for "which one answers the QR".
SECOND_PROFILE = "Map Presence Profile 2"
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
					"custom_pos_profiles": [
						{"pos_profile": PROFILE},
						{"pos_profile": SECOND_PROFILE},
					],
				}
			).insert(ignore_links=True, ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._wipe()
		for doctype, name in (
			("Branch", BRANCH),
			("POS Profile", PROFILE),
			("POS Profile", SECOND_PROFILE),
		):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _ensure_pos_profile(cls):
		for name in (PROFILE, SECOND_PROFILE):
			if frappe.db.exists("POS Profile", name):
				continue
			doc = frappe.get_doc({"doctype": "POS Profile", "company": cls.company})
			doc.name = name
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
		for extra in frappe.get_all(
			"Presence Till", filters={"pos_profile": SECOND_PROFILE}, pluck="name"
		):
			frappe.delete_doc("Presence Till", extra, force=True, ignore_permissions=True)
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

	def test_the_qr_skips_a_till_that_cannot_open_the_door(self):
		"""The freshest till is not always the one that can answer.

		A till reports an address only while its pairing server is listening. Taking the
		newest and giving up if it happened to be a broken one made a whole branch's
		pairing depend on its busiest till — while a healthy one stood beside it, able
		to do the job perfectly well, since every till at a branch watches the same
		network.
		"""
		# A SECOND till at this branch, reporting more recently than the first, but with
		# no address — its pairing server did not start.
		broken = frappe.get_doc(
			{
				"doctype": "Presence Till",
				"pos_profile": SECOND_PROFILE,
				"machine_name": "DESK-BROKEN",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Presence Till",
			broken.name,
			{"last_seen": add_to_date(now_datetime(), seconds=5), "local_url": None},
		)

		started = pairing.start(self.employee, BRANCH)

		# It used the healthy till's address, not the broken newer one.
		self.assertIn("192.168.1.5:7331", started["url"])
		frappe.delete_doc("Presence Till", broken.name, force=True, ignore_permissions=True)

	def test_when_no_till_can_open_the_door_it_says_so(self):
		"""And says how many are reporting, so it does not read as 'nothing is on'."""
		frappe.db.set_value("Presence Till", self.till_name, "local_url", None)

		with self.assertRaises(frappe.ValidationError) as caught:
			pairing.start(self.employee, BRANCH)

		message = str(caught.exception)
		self.assertIn("can accept phones right now", message)
		self.assertIn("Close the POS", message)
		# It must say how many ARE reporting, or it reads as "nothing is switched on"
		# and sends somebody hunting the wrong fault.
		self.assertIn("(1 reporting)", message)

	def test_the_company_comes_from_the_employee_not_the_operator(self):
		"""The bug that made pairing unusable on a site with more than one company.

		`start` used to prefer `frappe.defaults.get_user_default("Company")`, which is
		the operator's own setting and, when they have none, the SITE-WIDE default. On a
		site carrying twenty companies a manager was told wifi presence was switched off
		— for a company they were not looking at and whose employee they were not pairing.

		The whole suite missed it because setUp sets that default to the same company as
		the employee, so the wrong lookup returned the right answer. Here it deliberately
		does not, and points at a company that does not exist at all: if anything ever
		reads it again, this fails rather than passing by luck.
		"""
		frappe.defaults.set_user_default("Company", "A Company That Does Not Exist")
		try:
			started = pairing.start(self.employee, BRANCH)
			self.assertIn(started["code"], started["url"])

			stamped = frappe.db.get_value(
				"Presence Pairing Session",
				{"code": started["code"]},
				"custom_company",
			)
			self.assertEqual(
				stamped,
				frappe.db.get_value("Employee", self.employee, "company"),
				"the pairing must belong to the employee's company, not the operator's",
			)
		finally:
			frappe.defaults.set_user_default("Company", self.company)

	def test_pairing_a_phone_that_is_already_here_opens_the_shift(self):
		"""The gap that made the first day of every enrolment invisible.

		An arrival is a TRANSITION — a device that was absent and is now present. A
		phone paired at the counter never makes that transition: it was already on the
		wifi when the QR was scanned, so it is already in the branch's present set and
		nothing opens a session. The person's shift would not start until they left the
		shop and came back, which on the day they are enrolled means the whole day goes
		unrecorded — the exact day somebody is watching to see whether this works.
		"""
		# The till reports the phone BEFORE anyone pairs it, as it does for every device
		# on the network, paired or not.
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		self.assertFalse(
			self._open_session(),
			"an unknown phone must not open anything",
		)

		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		name = self._open_session()
		self.assertTrue(name, "the shift must open the moment the phone is paired")
		self.assertEqual(
			frappe.db.get_value("Presence Session", name, "device_key"), PHONE
		)

	def test_pairing_a_phone_that_is_NOT_on_the_wifi_opens_nothing(self):
		"""A QR sent by message, or scanned from the back office, is not evidence.

		The device has to be on the branch's live list. Otherwise pairing would clock
		somebody in from wherever they happen to be.
		"""
		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], TABLET)
		frappe.set_user("Administrator")

		self.assertFalse(self._open_session())

	def test_pairing_twice_does_not_open_a_second_shift(self):
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		first = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(first["code"], PHONE)
		frappe.set_user("Administrator")

		second = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(second["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertEqual(
			frappe.db.count(
				"Presence Session",
				{"employee": self.employee, "branch": BRANCH, "state": "Open"},
			),
			1,
		)

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

	def test_handing_a_phone_over_closes_the_first_persons_shift(self):
		"""Scanned for one person, then for another a minute later.

		Seen on qa-test: 09:44:21 and 09:45:13 on the same phone. The pairing moved
		correctly and the first employee's session was left open a day later — nothing
		else can ever close it, because the device now answers to somebody else, so the
		sweep attributes its departure to the new owner and the old shift hangs open
		for good. On screen that is a person still at work since yesterday morning.
		"""
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)

		first = pairing.start(self.other_employee, BRANCH)
		self._as_till()
		pairing.claim(first["code"], PHONE)
		frappe.set_user("Administrator")
		self.assertTrue(
			frappe.db.exists(
				"Presence Session", {"employee": self.other_employee, "state": "Open"}
			),
			"the first scan should have opened a shift for them",
		)

		second = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(second["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertFalse(
			frappe.db.exists(
				"Presence Session", {"employee": self.other_employee, "state": "Open"}
			),
			"the first employee was left on shift by a phone that is no longer theirs",
		)

	def test_a_handover_stops_the_old_owner_at_the_moment_not_at_midnight(self):
		"""`valid_to` is a Date, so without the moment BOTH pairings answer all day.

		The same trap `unpair` documents. Ownership decides whose attendance a stretch
		is, so for the rest of the handover day it was being resolved from two rows
		with nothing to choose between them.
		"""
		self._pair(PHONE, employee=self.other_employee)
		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		row = frappe.db.get_value(
			"Employee Device",
			{"device_key": PHONE, "employee": self.other_employee},
			["valid_to", "closed_at"],
			as_dict=True,
		)
		self.assertIsNotNone(row.valid_to)
		self.assertIsNotNone(
			row.closed_at, "the old pairing ran until midnight instead of stopping now"
		)
		self.assertEqual(
			service.employee_for(PHONE, self.company, now_datetime()), self.employee
		)

	def test_a_handover_leaves_the_old_owners_earlier_hours_alone(self):
		"""Only the tail is taken. This morning was genuinely theirs."""
		self._pair(PHONE, employee=self.other_employee, valid_from="2020-01-01")
		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertEqual(
			service.employee_for(
				PHONE, self.company, add_to_date(now_datetime(), days=-30)
			),
			self.other_employee,
		)

	def test_a_handover_does_not_clock_out_somebody_carrying_another_phone(self):
		"""They may still be standing at the till, seen through their other device."""
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)
		self._pair(PHONE, employee=self.other_employee)
		self._pair(TABLET, employee=self.other_employee)
		service._open_session(self._till(), self.other_employee, now_datetime(), TABLET)

		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertTrue(
			frappe.db.exists(
				"Presence Session", {"employee": self.other_employee, "state": "Open"}
			),
			"taking one phone clocked out somebody who still holds another",
		)

	def test_re_scanning_for_the_same_person_changes_nothing(self):
		"""The common accident — scanning twice for one person — must be a no-op."""
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		for _ in range(2):
			started = pairing.start(self.employee, BRANCH)
			self._as_till()
			pairing.claim(started["code"], PHONE)
			frappe.set_user("Administrator")

		self.assertEqual(
			frappe.db.count("Employee Device", {"device_key": PHONE, "valid_to": ("is", "not set")}),
			1,
		)
		self.assertTrue(self._open_session(), "their own shift was closed under them")

	def test_a_stretch_from_before_the_handover_belongs_to_the_old_owner(self):
		"""One phone, one hour, must not be credited to two people.

		Both pairings carry the same `valid_from` — a Date — so on the day of a handover
		each of them claims the whole day, and "newest wins" handed the morning to
		whoever picked the phone up at lunchtime. Seen on test: a manager and a colleague
		each showing the same three quarters of an hour, from one device.
		"""
		self._pair(
			PHONE,
			employee=self.other_employee,
			valid_from=str(now_datetime().date()),
		)
		# Between the two pairings: the phone was theirs at this moment.
		before = now_datetime()

		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertEqual(
			service.employee_for(PHONE, self.company, before),
			self.other_employee,
			"a stretch from before the swap was credited to the new owner",
		)
		self.assertEqual(
			service.employee_for(PHONE, self.company, now_datetime()),
			self.employee,
			"and after it, to the new one",
		)

	def test_a_back_dated_pairing_still_covers_that_whole_day(self):
		"""The rule above must not reach back and cut off an ordinary pairing.

		A pairing dated to last month says nothing about the time of day it began, so
		it has to go on covering the whole of every day it names — its `creation` is
		when somebody typed it in, not when the phone became theirs.
		"""
		self._pair(PHONE, employee=self.employee, valid_from="2020-01-01")

		morning = get_datetime("2020-01-01 06:00:00")

		self.assertEqual(
			service.employee_for(PHONE, self.company, morning), self.employee
		)

	def test_a_shift_is_not_kept_alive_by_a_phone_nobody_can_see(self):
		"""The other half of the same screen.

		Holding a second pairing is not evidence of being here. That phone was never on
		the wifi, so it could never be seen to LEAVE either — no departure was ever
		detected and the shift had nothing left that could close it.
		"""
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		self._pair(PHONE, employee=self.other_employee)
		# A second pairing for a phone that is not in the building.
		self._pair("neverseen0001", employee=self.other_employee)
		service._open_session(self._till(), self.other_employee, now_datetime(), PHONE)

		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertFalse(
			frappe.db.exists(
				"Presence Session", {"employee": self.other_employee, "state": "Open"}
			),
			"a pairing for an absent phone kept them on shift",
		)

	def test_a_shift_IS_kept_alive_by_a_phone_that_is_on_the_wifi(self):
		"""And the case the rule exists for. They are visibly still here."""
		service.ingest(self._till(), [PHONE, TABLET], now_datetime(), settled=True)
		self._pair(PHONE, employee=self.other_employee)
		self._pair(TABLET, employee=self.other_employee)
		service._open_session(self._till(), self.other_employee, now_datetime(), TABLET)

		started = pairing.start(self.employee, BRANCH)
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		self.assertTrue(
			frappe.db.exists(
				"Presence Session", {"employee": self.other_employee, "state": "Open"}
			),
			"somebody visibly on the wifi was clocked out",
		)

	def test_who_owns_a_device_is_never_left_to_the_database(self):
		"""Two rows can cover one moment; `get_all` adds no ORDER BY of its own.

		A pairing given a future end date is invisible to the duplicate guard, which
		looks only for rows with no `valid_to` — so the pair CAN overlap, and without an
		order the database picks whose attendance it is.
		"""
		self._pair(PHONE, employee=self.other_employee)
		frappe.db.set_value(
			"Employee Device",
			{"device_key": PHONE, "employee": self.other_employee},
			"valid_to",
			add_to_date(now_datetime(), days=30).date(),
		)
		self._pair(PHONE, employee=self.employee)

		answers = {
			service.employee_for(PHONE, self.company, now_datetime()) for _ in range(5)
		}

		self.assertEqual(len(answers), 1, "the same question gave different answers")
		self.assertEqual(answers.pop(), self.employee, "the newest pairing should win")

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

	def _sight(self, device_key, event, at):
		frappe.get_doc(
			{
				"doctype": "Presence Sighting",
				"custom_company": self.company,
				"branch": BRANCH,
				"device_key": device_key,
				"event": event,
				"server_time": at,
			}
		).insert(ignore_permissions=True)

	def _pair_at(self, device_key, at):
		"""A pairing made at a real moment today, the way scanning a QR makes one."""
		doc = frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": self.company,
				"employee": self.employee,
				"device_key": device_key,
				"valid_from": str(at.date()),
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Employee Device", doc.name, "creation", at, update_modified=False
		)

	def test_a_phone_does_not_draw_the_hours_before_it_was_paired(self):
		"""The map said 19:45; the shift said 20:25. Same screen, same person.

		`valid_from` is a Date, so ownership beginning at it began at MIDNIGHT — and every
		sighting from midnight until the scan got drawn as this person's time, hours when
		nothing could say whose pocket the phone was in. Attendance itself starts at the
		pairing on purpose. The picture of it has to agree.
		"""
		today = now_datetime().date()
		at = lambda h, m: datetime.combine(today, dt_time(h, m))

		self._sight(PHONE, "appeared", at(19, 45))
		self._pair_at(PHONE, at(20, 25))
		self._sight(PHONE, "gone", at(20, 53))

		result = api.timeline(self.employee, str(today), str(today))

		self.assertEqual(len(result["spans"]), 1)
		span = result["spans"][0]
		self.assertEqual(
			str(span["start"]),
			str(at(20, 25)),
			"the map drew time from before the phone was anybody's",
		)
		self.assertEqual(str(span["end"]), str(at(20, 53)))

	def test_a_back_dated_pairing_still_means_the_whole_of_that_day(self):
		"""The exception, and the reason the fix is not simply "use `creation`".

		A row deliberately dated to an earlier day is somebody saying this phone was
		theirs from then. Only a pairing recorded on the day it began can be narrowed to
		the moment it was recorded.
		"""
		today = now_datetime().date()
		yesterday = today - timedelta(days=1)
		at = lambda h, m: datetime.combine(today, dt_time(h, m))

		# Written today, but dated to yesterday — so today is entirely theirs.
		self._pair(PHONE, valid_from=str(yesterday))
		self._sight(PHONE, "appeared", at(6, 0))
		self._sight(PHONE, "gone", at(7, 0))

		result = api.timeline(self.employee, str(today), str(today))

		self.assertEqual(len(result["spans"]), 1, "a back-dated pairing drew nothing")
		self.assertEqual(str(result["spans"][0]["start"]), str(at(6, 0)))

	def test_a_person_with_no_history_gets_empty_lists_not_an_error(self):
		today = str(now_datetime().date())
		result = api.timeline(self.employee, today, today)
		self.assertEqual(result["sessions"], [])
		self.assertEqual(result["spans"], [])


if __name__ == "__main__":
	frappe.init(site="test")
