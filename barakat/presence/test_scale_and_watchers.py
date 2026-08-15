"""Several tills on one branch, and a shop with a lot of devices on the wifi.

Two things nothing else covers.

The first is that a branch normally has MORE THAN ONE till, and they all watch the same
room. They see overlapping sets of devices, they restart independently, and they report
on their own clocks. The rule the whole feature rests on is that the BRANCH is the unit
of truth, not the till: a device is here if ANY till can see it, and gone only when none
of them has for the wait. Every test in the first half is that sentence from a different
angle.

The second is scale. A branch is not four staff phones — it is every customer's phone,
the card reader, the printer, the manager's laptop. The cost of a sweep is set by the
size of the shop's network, not by the number of people employed, so the numbers here
are recorded rather than merely asserted: if a report of five hundred devices ever
starts taking seconds, that shows up as a failure rather than as a slow morning.
"""

import time
from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from barakat.presence import api, service
from barakat.presence.mode import WIFI

BRANCH = "Scale Presence Branch"
PROFILE_A = "Scale Presence Profile A"
PROFILE_B = "Scale Presence Profile B"
PHONE = "sca1edeadbee1"
TABLET = "sca1edeadbee2"

# Big enough to be a real shop's wifi and to make an O(n²) mistake obvious, small
# enough that the suite stays quick.
CROWD = 400


class TestManyWatchersAndDevices(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.employee = frappe.get_all("Employee", pluck="name", limit=1)[0]
		for profile in (PROFILE_A, PROFILE_B):
			cls._ensure_pos_profile(profile)

		if not frappe.db.exists("Branch", BRANCH):
			frappe.get_doc(
				{
					"doctype": "Branch",
					"branch": BRANCH,
					"custom_pos_company": cls.company,
					"custom_pos_profiles": [
						{"pos_profile": PROFILE_A},
						{"pos_profile": PROFILE_B},
					],
				}
			).insert(ignore_links=True, ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._wipe()
		if frappe.db.exists("Branch", BRANCH):
			frappe.delete_doc("Branch", BRANCH, force=True, ignore_permissions=True)
		for profile in (PROFILE_A, PROFILE_B):
			if frappe.db.exists("POS Profile", profile):
				frappe.delete_doc(
					"POS Profile", profile, force=True, ignore_permissions=True
				)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _ensure_pos_profile(cls, name):
		if frappe.db.exists("POS Profile", name):
			return
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
		frappe.db.delete("Employee Device", {"employee": cls.employee})
		for profile in (PROFILE_A, PROFILE_B):
			till = frappe.db.exists("Presence Till", {"pos_profile": profile})
			if till:
				user = frappe.db.get_value("Presence Till", till, "api_user")
				frappe.delete_doc(
					"Presence Till", till, force=True, ignore_permissions=True
				)
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
		self.till_a = self._live_till(PROFILE_A)
		self.till_b = self._live_till(PROFILE_B)

	# ---------------------------------------------------------------- helpers

	def _live_till(self, profile):
		api.request_join(profile, machine_name=f"DESK-{profile[-1]}")
		till = frappe.db.exists("Presence Till", {"pos_profile": profile})
		api.approve(till)
		api.request_join(profile)
		self._alive(till)
		return frappe.get_doc("Presence Till", till)

	def _alive(self, till_name, minutes_ago=0):
		frappe.db.set_value(
			"Presence Till",
			till_name,
			{
				"last_seen": add_to_date(now_datetime(), minutes=-minutes_ago),
				"is_settled": 1,
				"is_blind": 0,
			},
		)

	def _pair(self, device_key):
		frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": self.company,
				"employee": self.employee,
				"device_key": device_key,
				"valid_from": "2020-01-01",
			}
		).insert(ignore_permissions=True)

	def _open_session(self):
		return frappe.db.exists(
			"Presence Session",
			{"employee": self.employee, "branch": BRANCH, "state": "Open"},
		)

	def _age(self, device_key, minutes):
		frappe.db.set_value(
			"Presence Live Device",
			f"{BRANCH}::{device_key}",
			"last_seen",
			add_to_date(now_datetime(), minutes=-minutes),
		)

	def _crowd(self, count=CROWD):
		return [f"c{index:012x}" for index in range(count)]

	# ------------------------------------------------------- more than one watcher

	def test_two_tills_seeing_the_same_phone_open_one_shift(self):
		"""The branch is the unit of truth. Two sets of eyes are not two people."""
		self._pair(PHONE)
		service.ingest(self.till_a, [PHONE], now_datetime(), settled=True)
		service.ingest(self.till_b, [PHONE], now_datetime(), settled=True)

		self.assertEqual(
			frappe.db.count(
				"Presence Session",
				{"employee": self.employee, "branch": BRANCH, "state": "Open"},
			),
			1,
		)

	def test_one_till_seeing_a_phone_is_enough_to_keep_it_present(self):
		"""Tills sit in different corners; one of them not seeing a phone means nothing."""
		self._pair(PHONE)
		service.ingest(self.till_a, [PHONE], now_datetime(), settled=True)
		# B can see the room but not that phone, and says so repeatedly.
		for _ in range(3):
			service.ingest(self.till_b, [], now_datetime(), settled=True)

		self._alive(self.till_a.name)
		self._alive(self.till_b.name)
		service.sweep(BRANCH, self.company)

		self.assertTrue(
			self._open_session(), "a till that could not see the phone sent somebody home"
		)

	def test_a_second_till_going_quiet_does_not_send_anyone_home(self):
		self._pair(PHONE)
		service.ingest(self.till_a, [PHONE], now_datetime(), settled=True)

		self._alive(self.till_a.name)
		self._alive(self.till_b.name, minutes_ago=120)  # B unplugged two hours ago
		service.sweep(BRANCH, self.company)

		self.assertTrue(self._open_session())

	def test_departures_still_work_while_one_till_is_blind(self):
		"""A healthy till is enough cover. Waiting for ALL of them would mean one broken
		machine freezes the branch's attendance indefinitely."""
		self._pair(PHONE)
		service.ingest(self.till_a, [PHONE], now_datetime(), settled=True)

		self._age(PHONE, 40)
		self._alive(self.till_a.name)
		frappe.db.set_value("Presence Till", self.till_b.name, "is_blind", 1)
		service.sweep(BRANCH, self.company)

		self.assertFalse(self._open_session())

	def test_every_till_going_quiet_freezes_the_branch(self):
		"""Nobody can see the shop, so nobody may be marked as having left it.

		A SHORT silence, deliberately. A branch that stays dark is eventually written
		off — otherwise the shifts open when the last till is switched off at closing
		time would never close at all. What this guards is that the freeze is real
		while the outage is plausibly a restart.
		"""
		self._pair(PHONE)
		service.ingest(self.till_a, [PHONE], now_datetime(), settled=True)

		self._age(PHONE, 20)
		self._alive(self.till_a.name, minutes_ago=20)
		self._alive(self.till_b.name, minutes_ago=20)
		service.sweep(BRANCH, self.company)

		self.assertTrue(
			self._open_session(), "an unreachable branch sent its whole shop home"
		)

	def test_sequence_numbers_are_per_till_and_do_not_collide(self):
		"""Two tills counting independently must not refuse each other's reports."""
		till_a = frappe.get_doc("Presence Till", self.till_a.name)
		till_b = frappe.get_doc("Presence Till", self.till_b.name)

		service.ingest(till_a, [PHONE], now_datetime(), settled=True)
		frappe.db.set_value("Presence Till", till_a.name, "last_seq", 900)
		frappe.db.set_value("Presence Till", till_b.name, "last_seq", 5)

		self.assertEqual(
			frappe.db.get_value("Presence Till", till_b.name, "last_seq"),
			5,
			"one till's counter overwrote another's",
		)

	def test_a_phone_moving_between_tills_stays_one_shift(self):
		"""Walking from the front counter to the back office is not a new day."""
		self._pair(PHONE)
		service.ingest(self.till_a, [PHONE], now_datetime(), settled=True)
		opened = self._open_session()

		service.ingest(self.till_a, [], now_datetime(), settled=True)
		service.ingest(self.till_b, [PHONE], now_datetime(), settled=True)

		self.assertEqual(self._open_session(), opened, "the shift was restarted")

	# ------------------------------------------------------------ a crowded shop

	def test_a_shop_full_of_strangers_opens_no_shifts(self):
		"""Four hundred customers' phones belong to nobody and must stay that way."""
		service.ingest(self.till_a, self._crowd(), now_datetime(), settled=True)
		self.assertEqual(
			frappe.db.count("Presence Session", {"branch": BRANCH}),
			0,
			"an unpaired phone opened a shift",
		)

	def test_one_staff_phone_is_found_among_four_hundred(self):
		self._pair(PHONE)
		service.ingest(
			self.till_a, self._crowd() + [PHONE], now_datetime(), settled=True
		)
		self.assertTrue(self._open_session())

	def test_only_changes_are_logged_however_often_a_till_reports(self):
		"""The whole reason a branch writes ~60 rows a day instead of ~43,000."""
		crowd = self._crowd(50)
		service.ingest(self.till_a, crowd, now_datetime(), settled=True)
		after_first = frappe.db.count("Presence Sighting", {"branch": BRANCH})

		for _ in range(10):
			service.ingest(self.till_a, crowd, now_datetime(), settled=True)

		self.assertEqual(
			frappe.db.count("Presence Sighting", {"branch": BRANCH}),
			after_first,
			"a report that changed nothing still wrote rows",
		)

	def test_a_report_larger_than_the_cap_is_refused_not_trimmed(self):
		"""Silently dropping half a shop's devices would read as those people leaving."""
		with self.assertRaises(frappe.ValidationError):
			api._clean_devices(self._crowd(api.MAX_BODY_DEVICES + 1), self.till_a)

	def test_the_same_device_twice_in_one_report_counts_once(self):
		cleaned = api._clean_devices([PHONE, PHONE, PHONE.upper()], self.till_a)
		self.assertEqual(cleaned, [PHONE])

	def test_an_overlong_device_id_is_cut_to_the_column(self):
		cleaned = api._clean_devices(["z" * 200], self.till_a)
		self.assertEqual(len(cleaned[0]), 64)

	# ------------------------------------------------------------------- timing

	def test_a_crowded_report_stays_well_under_a_sweep_interval(self):
		"""A till reports every two seconds. If ingest ever takes longer than that, the
		reports queue up and the branch falls behind reality — so the budget is not
		aesthetic. Generous here because CI machines are slow and shared.
		"""
		crowd = self._crowd()
		service.ingest(self.till_a, crowd, now_datetime(), settled=True)  # warm

		started = time.perf_counter()
		service.ingest(self.till_a, crowd, now_datetime(), settled=True)
		elapsed = time.perf_counter() - started

		print(f"\n  ingest of {CROWD} devices (no change): {elapsed * 1000:.0f}ms")
		self.assertLess(elapsed, 2.0, f"ingest took {elapsed:.2f}s, past a sweep interval")

	def test_a_crowded_arrival_stays_within_budget(self):
		"""The expensive case: every device is new, so every one is a decision."""
		started = time.perf_counter()
		service.ingest(self.till_a, self._crowd(), now_datetime(), settled=True)
		elapsed = time.perf_counter() - started

		print(f"\n  ingest of {CROWD} devices (all new): {elapsed * 1000:.0f}ms")
		self.assertLess(elapsed, 5.0, f"first sight of {CROWD} devices took {elapsed:.2f}s")

	def test_a_crowded_sweep_stays_within_its_minute(self):
		"""The sweep runs every minute across every branch of every company. If one
		branch of four hundred devices cannot finish inside that, the job overlaps
		itself."""
		crowd = self._crowd()
		service.ingest(self.till_a, crowd, now_datetime(), settled=True)
		for device in crowd:
			self._age(device, 40)
		self._alive(self.till_a.name)

		started = time.perf_counter()
		service.sweep(BRANCH, self.company)
		elapsed = time.perf_counter() - started

		print(f"\n  sweep aging out {CROWD} devices: {elapsed * 1000:.0f}ms")
		self.assertLess(elapsed, 10.0, f"sweep took {elapsed:.2f}s")
