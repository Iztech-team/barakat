"""A till says it is alive, in a shop that does not use wifi attendance.

The heartbeat and the wifi sweep were one thing until now, and that is the whole subject
of this file. A till reported its branch's phones, and reporting was refused outright
unless the company had switched wifi presence on - so a shop that had not switched it on
had no way at all to tell the server it existed. The tills board needs exactly that: not
who is in the building, just which machines are up.

So `report` now carries two separable halves. The health half is always allowed. The
sightings half is allowed only in wifi mode, and is DROPPED rather than refused when it
is not - a till whose company flipped to manual mid-shift must not start failing, it must
just stop being believed about people.

Everything that made the old boundary safe is still here and is tested here again from
the manual-mode side, because a relaxation is exactly where a gate quietly goes missing:
a suspended till still cannot report, a replayed sequence is still refused, and a till
that no human approved still gets no key.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.presence import api, watch
from barakat.presence.mode import MANUAL, WIFI

BRANCH = "Heartbeat Presence Branch"
PROFILE = "Heartbeat Presence Profile"
PHONE = "hb01deadbeef01"


class TestTillHeartbeat(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
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
		"""Exists, and nothing more - see the note in test_end_to_end."""
		if frappe.db.exists("POS Profile", PROFILE):
			return
		doc = frappe.get_doc({"doctype": "POS Profile", "company": cls.company})
		doc.name = PROFILE
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)

	@classmethod
	def _wipe(cls):
		for doctype in ("Presence Sighting", "Presence Live Device", "Presence Session"):
			frappe.db.delete(doctype, {"branch": BRANCH})
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
		watch.forget(self.company)

	def tearDown(self):
		frappe.set_user("Administrator")
		watch.forget(self.company)

	# ---------------------------------------------------------------- helpers

	def _set_mode(self, mode, **extra):
		frappe.get_doc(
			{
				"doctype": "Presence Settings",
				"custom_company": self.company,
				"mode": mode,
				**extra,
			}
		).insert(ignore_permissions=True)

	def _enrol(self, mode=MANUAL, **extra):
		"""A till that has joined, been approved, and collected its key."""
		self._set_mode(mode, **extra)
		api.request_join(PROFILE, machine_name="DESK-HB-01", wants_heartbeat=True)
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		api.approve(till)
		result = api.request_join(
			PROFILE, machine_name="DESK-HB-01", wants_heartbeat=True
		)
		return till, result

	def _as_till(self, till):
		frappe.set_user(frappe.db.get_value("Presence Till", till, "api_user"))

	def _row(self, till, field):
		return frappe.db.get_value("Presence Till", till, field)

	# ------------------------------------------------------ joining in manual mode

	def test_a_manual_shop_hands_its_till_a_key(self):
		"""The whole point. Without this a manual shop's till can never authenticate."""
		self._set_mode(MANUAL)
		api.request_join(PROFILE, machine_name="DESK-HB-01", wants_heartbeat=True)
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		api.approve(till)

		result = api.request_join(
			PROFILE, machine_name="DESK-HB-01", wants_heartbeat=True
		)

		self.assertEqual(result["status"], "approved")
		self.assertTrue(result["api_key"])
		self.assertTrue(result["api_secret"])

	def test_an_older_pos_is_still_told_the_shop_is_off(self):
		"""Tills auto-update on their own schedule, so old builds outlive the server.

		An older build has no idea the sweep can be off. Given a key in a manual shop it
		would scan the branch network every two seconds for sightings this server throws
		away - so it is answered exactly as it always was.
		"""
		self._set_mode(MANUAL)
		api.request_join(PROFILE, machine_name="DESK-HB-01")
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		api.approve(till)

		result = api.request_join(PROFILE, machine_name="DESK-HB-01")

		self.assertEqual(result["status"], "off")
		self.assertNotIn("api_secret", result)

	def test_a_flag_that_arrived_as_text_is_read_as_text(self):
		"""Over HTTP `False` arrives as the string "false", which Python calls true."""
		self._set_mode(MANUAL)
		api.request_join(PROFILE, machine_name="DESK-HB-01", wants_heartbeat="false")
		till = frappe.db.exists("Presence Till", {"pos_profile": PROFILE})
		api.approve(till)

		result = api.request_join(
			PROFILE, machine_name="DESK-HB-01", wants_heartbeat="false"
		)

		self.assertEqual(result["status"], "off")

	def test_a_report_repeats_whether_the_sweep_should_run(self):
		"""So flipping the mode in the Admin Panel reaches a till nobody restarted."""
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		self.assertFalse(api.report(seq=1)["wifi_enabled"])

	def test_a_manual_shops_key_says_the_wifi_half_is_off(self):
		"""The till must not start sweeping the network just because it got a key."""
		_, result = self._enrol(MANUAL)

		self.assertFalse(result["settings"]["wifi_enabled"])

	def test_a_wifi_shops_key_says_the_wifi_half_is_on(self):
		_, result = self._enrol(WIFI)

		self.assertTrue(result["settings"]["wifi_enabled"])

	def test_a_manual_shops_till_still_waits_for_a_human(self):
		"""Relaxing the mode gate must not relax the approval gate."""
		self._set_mode(MANUAL)

		result = api.request_join(
			PROFILE, machine_name="DESK-HB-01", wants_heartbeat=True
		)

		self.assertEqual(result["status"], "pending")
		self.assertNotIn("api_secret", result)

	# ------------------------------------------------------ reporting in manual mode

	def test_a_manual_shops_till_can_say_it_is_alive(self):
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		result = api.report(seq=1)

		self.assertTrue(result["ok"])
		frappe.set_user("Administrator")
		self.assertTrue(self._row(till, "last_seen"))

	def test_a_manual_shops_sightings_are_dropped_not_refused(self):
		"""A shop that does not use wifi attendance records nobody's attendance."""
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		api.report(devices=[{"id": PHONE}], seq=1)

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Presence Sighting", {"branch": BRANCH}), 0)
		self.assertEqual(frappe.db.count("Presence Live Device", {"branch": BRANCH}), 0)

	def test_a_wifi_shops_sightings_are_still_recorded(self):
		"""The guard above must not have turned the feature off for everybody."""
		till, _ = self._enrol(WIFI)
		self._as_till(till)

		api.report(devices=[{"id": PHONE}], seq=1)

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Presence Live Device", {"branch": BRANCH}), 1)

	def test_a_suspended_till_still_cannot_report_in_manual_mode(self):
		till, _ = self._enrol(MANUAL)
		api.suspend(till)
		self._as_till(till)

		with self.assertRaises(frappe.PermissionError):
			api.report(seq=1)

	def test_a_replayed_report_is_still_refused_in_manual_mode(self):
		till, _ = self._enrol(MANUAL)
		self._as_till(till)
		api.report(seq=7)

		with self.assertRaises(frappe.DuplicateEntryError):
			api.report(seq=7)

	def test_a_stranger_still_cannot_report(self):
		"""No key, no report - the first check in the method, unchanged."""
		self._enrol(MANUAL)
		frappe.set_user("Administrator")

		with self.assertRaises(frappe.PermissionError):
			api.report(seq=1)

	# ---------------------------------------------------------------- health

	def test_a_till_reports_what_is_stuck_in_its_queue(self):
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		api.report(seq=1, health={"queued": 12})

		frappe.set_user("Administrator")
		self.assertEqual(self._row(till, "queued_orders"), 12)

	def test_a_till_reports_the_version_it_is_running(self):
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		api.report(seq=1, pos_version="2.39.1")

		frappe.set_user("Administrator")
		self.assertEqual(self._row(till, "pos_version"), "2.39.1")

	def test_an_error_is_kept_with_the_moment_it_happened(self):
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		api.report(seq=1, health={"last_error": "printer offline"})

		frappe.set_user("Administrator")
		self.assertEqual(self._row(till, "last_error"), "printer offline")
		self.assertTrue(self._row(till, "last_error_at"))

	def test_an_error_that_clears_is_forgotten(self):
		"""Otherwise a till that recovered wears yesterday's fault for ever."""
		till, _ = self._enrol(MANUAL)
		self._as_till(till)
		api.report(seq=1, health={"last_error": "printer offline"})

		api.report(seq=2, health={"last_error": None})

		frappe.set_user("Administrator")
		self.assertEqual(self._row(till, "last_error") or "", "")
		self.assertIsNone(self._row(till, "last_error_at"))

	def test_a_runaway_error_cannot_overflow_the_column(self):
		"""A stack trace is a plausible thing for a till to send. It must not 500."""
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		api.report(seq=1, health={"last_error": "x" * 5000})

		frappe.set_user("Administrator")
		self.assertLessEqual(len(self._row(till, "last_error")), api.MAX_ERROR_CHARS)

	def test_health_that_says_nothing_leaves_what_was_known(self):
		"""An older POS sends no health at all. It must not erase the row."""
		till, _ = self._enrol(MANUAL)
		self._as_till(till)
		api.report(seq=1, health={"queued": 4}, pos_version="2.39.1")

		api.report(seq=2)

		frappe.set_user("Administrator")
		self.assertEqual(self._row(till, "queued_orders"), 4)
		self.assertEqual(self._row(till, "pos_version"), "2.39.1")

	def test_a_queue_that_empties_is_recorded_as_empty(self):
		"""Zero is a real answer here, and the opposite of 'said nothing'."""
		till, _ = self._enrol(MANUAL)
		self._as_till(till)
		api.report(seq=1, health={"queued": 9})

		api.report(seq=2, health={"queued": 0})

		frappe.set_user("Administrator")
		self.assertEqual(self._row(till, "queued_orders"), 0)

	# ------------------------------------------------------- adaptive heartbeat

	def test_a_till_nobody_is_watching_keeps_its_own_pace(self):
		till, _ = self._enrol(MANUAL)
		self._as_till(till)

		result = api.report(seq=1)

		self.assertEqual(result["next_heartbeat_s"], 30)

	def test_a_watched_till_is_told_to_come_back_sooner(self):
		till, _ = self._enrol(MANUAL)
		watch.mark_watching(self.company)
		self._as_till(till)

		result = api.report(seq=1)

		self.assertEqual(result["next_heartbeat_s"], watch.WATCHED_HEARTBEAT_S)

	def test_watching_never_slows_a_shop_that_already_reports_faster(self):
		"""min(), not a replacement - a 1s shop must not be pushed back up to 3s."""
		till, _ = self._enrol(MANUAL, heartbeat_s=1)
		watch.mark_watching(self.company)
		self._as_till(till)

		result = api.report(seq=1)

		self.assertEqual(result["next_heartbeat_s"], 1)

	def test_a_watch_that_ran_out_stops_hurrying_the_till(self):
		till, _ = self._enrol(MANUAL)
		watch.mark_watching(self.company)
		watch.forget(self.company)
		self._as_till(till)

		result = api.report(seq=1)

		self.assertEqual(result["next_heartbeat_s"], 30)

	def test_watching_one_shop_does_not_hurry_another(self):
		till, _ = self._enrol(MANUAL)
		watch.mark_watching("Some Other Company")
		self._as_till(till)

		result = api.report(seq=1)

		self.assertEqual(result["next_heartbeat_s"], 30)
