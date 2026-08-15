"""One branch, one day — the other axis of the attendance picture.

`timeline` answers "one person, many days". This answers "many people, one day", which
is the question a manager standing in a shop actually has. The two draw the same strips
from the same data, so the risk here is not the drawing: it is that a person, a phone or
a shift quietly falls out of the answer, and an empty row is indistinguishable from a row
nobody asked for.

Every test below is therefore about WHO appears and WHAT is attributed to them.
"""

from datetime import datetime, time as dt_time, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from barakat.presence import api
from barakat.presence.mode import WIFI

BRANCH = "Day Presence Branch"
OTHER_BRANCH = "Day Presence Branch Two"
PHONE = "day0deadbeef01"
TABLET = "day0deadbeef02"
STRANGER = "day0cafef00d99"


class TestBranchDay(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		people = frappe.get_all(
			"Employee", filters={"status": "Active"}, pluck="name", limit=3
		)
		cls.alice = people[0]
		cls.bob = people[1] if len(people) > 1 else people[0]
		cls.carol = people[2] if len(people) > 2 else cls.alice

		for name in (BRANCH, OTHER_BRANCH):
			if not frappe.db.exists("Branch", name):
				frappe.get_doc(
					{
						"doctype": "Branch",
						"branch": name,
						"custom_pos_company": cls.company,
					}
				).insert(ignore_links=True, ignore_permissions=True)

		if not frappe.db.exists("Presence Settings", {"custom_company": cls.company}):
			frappe.get_doc(
				{
					"doctype": "Presence Settings",
					"custom_company": cls.company,
					"mode": WIFI,
				}
			).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._wipe()
		for name in (BRANCH, OTHER_BRANCH):
			if frappe.db.exists("Branch", name):
				frappe.delete_doc("Branch", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _wipe(cls):
		for doctype in (
			"Presence Session",
			"Presence Sighting",
			"Presence Live Device",
			"Employee Device",
		):
			frappe.db.delete(doctype, {"custom_company": cls.company})
		# The native branch link is shared state across tests in this file.
		for employee in (cls.alice, cls.bob, cls.carol):
			frappe.db.set_value("Employee", employee, "branch", None)
			frappe.db.delete(
				"POS Employee Branch", {"parent": employee, "parenttype": "Employee"}
			)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self._wipe()
		self.today = now_datetime().date()

	# ------------------------------------------------------------------ helpers

	def at(self, hour, minute=0):
		return datetime.combine(self.today, dt_time(hour, minute))

	def _assign_native(self, employee, branch=BRANCH):
		frappe.db.set_value("Employee", employee, "branch", branch)

	def _assign_pos(self, employee, branch=BRANCH):
		doc = frappe.get_doc("Employee", employee)
		doc.append("custom_pos_branches", {"branch": branch})
		doc.save(ignore_permissions=True)

	def _pair(self, employee, device_key, valid_from="2020-01-01", valid_to=None):
		doc = frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": self.company,
				"employee": employee,
				"device_key": device_key,
				"valid_from": valid_from,
			}
		).insert(ignore_permissions=True)
		if valid_to:
			frappe.db.set_value("Employee Device", doc.name, "valid_to", valid_to)
		return doc

	def _session(self, employee, start, end=None, branch=BRANCH, device=PHONE):
		return frappe.get_doc(
			{
				"doctype": "Presence Session",
				"custom_company": self.company,
				"branch": branch,
				"employee": employee,
				"in_time": start,
				"out_time": end,
				"state": "Closed" if end else "Open",
				"device": device,
			}
		).insert(ignore_permissions=True)

	def _sight(self, device_key, event, when, branch=BRANCH):
		frappe.get_doc(
			{
				"doctype": "Presence Sighting",
				"custom_company": self.company,
				"branch": branch,
				"device_key": device_key,
				"event": event,
				"server_time": when,
			}
		).insert(ignore_permissions=True)

	def _day(self, day=None):
		return api.branch_day(BRANCH, str(day or self.today))

	def _row(self, result, employee):
		return next((r for r in result["staff"] if r["employee"] == employee), None)

	# ------------------------------------------------------------- who is listed

	def test_someone_assigned_by_the_native_branch_field_is_listed(self):
		self._assign_native(self.alice)
		self.assertIsNotNone(self._row(self._day(), self.alice))

	def test_someone_assigned_only_by_the_pos_child_table_is_listed(self):
		"""The half a native-field-only query would silently lose.

		ERPNext's Employee holds ONE branch link, so everybody past the first shop they
		work at lives in `POS Employee Branch`. On a multi-branch shop that is most of
		the staff.
		"""
		self._assign_pos(self.bob)
		self.assertIsNotNone(self._row(self._day(), self.bob))

	def test_somebody_at_another_branch_is_not_listed(self):
		self._assign_native(self.carol, OTHER_BRANCH)
		self.assertIsNone(self._row(self._day(), self.carol))

	def test_an_assigned_person_with_no_data_still_gets_a_row(self):
		"""An empty row is an answer: assigned here, no sign of them today.

		Dropping them would make "absent" and "never paired a phone" look identical on
		screen, and telling those apart is most of what this feature is for.
		"""
		self._assign_native(self.alice)

		row = self._row(self._day(), self.alice)

		self.assertEqual(row["sessions"], [])
		self.assertEqual(row["spans"], [])

	def test_somebody_who_worked_here_but_has_since_moved_still_appears(self):
		"""A day must not disagree with itself.

		They are no longer assigned to this branch, but they were here on the day being
		looked at and the shift proves it. Listing only current staff would delete
		history every time somebody transfers.
		"""
		self._assign_native(self.carol, OTHER_BRANCH)
		self._session(self.carol, self.at(9), self.at(17))

		row = self._row(self._day(), self.carol)

		self.assertIsNotNone(row, "a shift was recorded here and its owner vanished")
		self.assertFalse(row["stillHere"])
		self.assertEqual(len(row["sessions"]), 1)

	def test_current_staff_are_marked_as_still_here(self):
		self._assign_native(self.alice)
		self.assertTrue(self._row(self._day(), self.alice)["stillHere"])

	def test_nobody_assigned_and_nothing_recorded_is_an_empty_list(self):
		result = self._day()
		self.assertEqual(result["staff"], [])
		self.assertEqual(result["branch"], BRANCH)

	def test_a_person_assigned_twice_over_appears_once(self):
		# Native field AND the child table naming the same branch.
		self._assign_native(self.alice)
		self._assign_pos(self.alice)

		rows = [r for r in self._day()["staff"] if r["employee"] == self.alice]

		self.assertEqual(len(rows), 1)

	def test_the_list_is_ordered_by_name_so_it_does_not_reshuffle(self):
		"""A row that moves between refreshes is a row nobody can point at."""
		self._assign_native(self.alice)
		self._assign_pos(self.bob)

		names = [r["employeeName"] for r in self._day()["staff"]]

		self.assertEqual(names, sorted(names, key=str.lower))

	# ---------------------------------------------------------------- the shifts

	def test_a_shift_on_the_day_comes_back_on_its_owners_row(self):
		self._assign_native(self.alice)
		self._session(self.alice, self.at(9), self.at(17))

		row = self._row(self._day(), self.alice)

		self.assertEqual(len(row["sessions"]), 1)
		self.assertEqual(row["sessions"][0]["inTime"][:19], str(self.at(9))[:19])
		self.assertFalse(row["sessions"][0]["open"])

	def test_an_open_shift_is_marked_open_rather_than_given_an_end(self):
		self._assign_native(self.alice)
		self._session(self.alice, self.at(9))

		session = self._row(self._day(), self.alice)["sessions"][0]

		self.assertTrue(session["open"])
		self.assertIsNone(session["outTime"])

	def test_yesterdays_shift_is_not_on_todays_page(self):
		self._assign_native(self.alice)
		yesterday = datetime.combine(
			self.today - timedelta(days=1), dt_time(9, 0)
		)
		self._session(self.alice, yesterday, yesterday + timedelta(hours=8))

		self.assertEqual(self._row(self._day(), self.alice)["sessions"], [])

	def test_a_shift_at_another_branch_is_not_on_this_branchs_page(self):
		self._assign_native(self.alice)
		self._session(self.alice, self.at(9), self.at(17), branch=OTHER_BRANCH)

		self.assertEqual(self._row(self._day(), self.alice)["sessions"], [])

	def test_two_shifts_in_one_day_both_come_back(self):
		# Morning, went home, came back for the evening.
		self._assign_native(self.alice)
		self._session(self.alice, self.at(8), self.at(12))
		self._session(self.alice, self.at(17), self.at(21))

		self.assertEqual(len(self._row(self._day(), self.alice)["sessions"]), 2)

	def test_a_night_shift_is_returned_on_the_day_it_STARTED(self):
		"""Splitting it would invent two shifts out of one night."""
		self._assign_native(self.alice)
		self._session(
			self.alice,
			self.at(22),
			datetime.combine(self.today + timedelta(days=1), dt_time(6, 0)),
		)

		self.assertEqual(len(self._row(self._day(), self.alice)["sessions"]), 1)

	# ----------------------------------------------------------------- the spans

	def test_a_phones_stretch_lands_on_its_owners_row(self):
		self._assign_native(self.alice)
		self._pair(self.alice, PHONE)
		self._sight(PHONE, "appeared", self.at(9))
		self._sight(PHONE, "gone", self.at(17))

		spans = self._row(self._day(), self.alice)["spans"]

		self.assertEqual(len(spans), 1)
		self.assertEqual(spans[0]["deviceKey"], PHONE)
		self.assertEqual(spans[0]["start"][:19], str(self.at(9))[:19])

	def test_two_phones_on_one_person_stay_two_stretches(self):
		"""The whole reason spans exist beside sessions."""
		self._assign_native(self.alice)
		self._pair(self.alice, PHONE)
		self._pair(self.alice, TABLET)
		for device in (PHONE, TABLET):
			self._sight(device, "appeared", self.at(9))
			self._sight(device, "gone", self.at(17))

		spans = self._row(self._day(), self.alice)["spans"]

		self.assertEqual({s["deviceKey"] for s in spans}, {PHONE, TABLET})

	def test_two_peoples_phones_do_not_land_on_one_row(self):
		self._assign_native(self.alice)
		self._assign_pos(self.bob)
		self._pair(self.alice, PHONE)
		self._pair(self.bob, TABLET)
		for device in (PHONE, TABLET):
			self._sight(device, "appeared", self.at(9))
			self._sight(device, "gone", self.at(17))

		result = self._day()

		self.assertEqual(
			[s["deviceKey"] for s in self._row(result, self.alice)["spans"]], [PHONE]
		)
		self.assertEqual(
			[s["deviceKey"] for s in self._row(result, self.bob)["spans"]], [TABLET]
		)

	def test_an_unpaired_phone_belongs_to_nobody_and_appears_on_no_row(self):
		self._assign_native(self.alice)
		self._sight(STRANGER, "appeared", self.at(9))
		self._sight(STRANGER, "gone", self.at(17))

		result = self._day()

		self.assertTrue(all(not r["spans"] for r in result["staff"]))

	def test_a_phone_handed_over_at_noon_splits_between_two_people(self):
		"""Attributed by WHO HELD IT then, not who holds it now.

		Otherwise transferring a phone rewrites every earlier day it appears on, and
		gives one person a shift another person worked.
		"""
		yesterday = str(self.today - timedelta(days=1))
		self._assign_native(self.alice)
		self._assign_pos(self.bob)
		# Alice held it until yesterday; Bob has it from today.
		self._pair(self.alice, PHONE, valid_from="2020-01-01", valid_to=yesterday)
		self._pair(self.bob, PHONE, valid_from=str(self.today))
		self._sight(PHONE, "appeared", self.at(9))
		self._sight(PHONE, "gone", self.at(17))

		result = self._day()

		self.assertEqual(self._row(result, self.alice)["spans"], [])
		self.assertEqual(len(self._row(result, self.bob)["spans"]), 1)

	def test_a_stretch_running_past_midnight_is_cut_at_the_days_edge(self):
		"""A day is midnight to midnight; a strip cannot draw past its own end.

		Asked about YESTERDAY on purpose. An unclosed stretch is drawn up to `now`, so
		a night shift starting at 22:00 "today" has not happened yet when the suite runs
		in the morning, and the span collapses to nothing — a green test that proves the
		clock, not the code.
		"""
		self._assign_native(self.alice)
		self._pair(self.alice, PHONE)
		yesterday = self.today - timedelta(days=1)
		self._sight(PHONE, "appeared", datetime.combine(yesterday, dt_time(22, 0)))
		self._sight(PHONE, "gone", datetime.combine(self.today, dt_time(6, 0)))

		spans = self._row(self._day(yesterday), self.alice)["spans"]

		self.assertEqual(len(spans), 1)
		self.assertEqual(spans[0]["start"][:19], str(datetime.combine(yesterday, dt_time(22, 0)))[:19])
		self.assertLessEqual(spans[0]["end"][:10], str(yesterday))

	def test_yesterdays_sightings_do_not_leak_into_today(self):
		self._assign_native(self.alice)
		self._pair(self.alice, PHONE)
		y = self.today - timedelta(days=1)
		self._sight(PHONE, "appeared", datetime.combine(y, dt_time(9, 0)))
		self._sight(PHONE, "gone", datetime.combine(y, dt_time(17, 0)))

		self.assertEqual(self._row(self._day(), self.alice)["spans"], [])

	# ------------------------------------------------------------- the day itself

	def test_a_day_in_the_future_is_empty_rather_than_an_error(self):
		"""The screen refuses to ask, but the endpoint must not fall over if it does."""
		self._assign_native(self.alice)

		result = api.branch_day(BRANCH, str(self.today + timedelta(days=3)))

		self.assertTrue(all(not r["sessions"] and not r["spans"] for r in result["staff"]))

	def test_the_day_asked_for_is_the_day_answered(self):
		result = self._day()
		self.assertEqual(result["day"], str(self.today))

	def test_a_branch_with_no_company_is_refused_rather_than_scoped_to_nothing(self):
		"""A blank company marker matches every tenant — never let it through."""
		name = "Day Presence Branch Companyless"
		if not frappe.db.exists("Branch", name):
			frappe.get_doc({"doctype": "Branch", "branch": name}).insert(
				ignore_links=True, ignore_permissions=True
			)
		self.addCleanup(
			frappe.delete_doc, "Branch", name, force=True, ignore_permissions=True
		)

		with self.assertRaises(frappe.ValidationError):
			api.branch_day(name, str(self.today))

	def test_the_detail_horizon_is_reported_so_a_gap_is_not_read_as_absence(self):
		self._assign_native(self.alice)
		self._pair(self.alice, PHONE)
		self._sight(PHONE, "appeared", self.at(9))

		self.assertTrue(self._day()["detailFrom"])

	# ----------------------------------------------------- agreement with timeline

	def test_it_tells_the_same_story_as_the_per_person_timeline(self):
		"""Two ways of asking, one answer.

		They share the ownership and folding code precisely so this holds; the test is
		here to catch the day somebody optimises one path and not the other.
		"""
		self._assign_native(self.alice)
		self._pair(self.alice, PHONE)
		self._sight(PHONE, "appeared", self.at(9))
		self._sight(PHONE, "gone", self.at(17))
		self._session(self.alice, self.at(9), self.at(17))

		from_day = self._row(self._day(), self.alice)
		from_timeline = api.timeline(self.alice, str(self.today), str(self.today))

		self.assertEqual(
			[(s["deviceKey"], s["start"], s["end"]) for s in from_day["spans"]],
			[(s["deviceKey"], s["start"], s["end"]) for s in from_timeline["spans"]],
		)
		self.assertEqual(
			[s["inTime"] for s in from_day["sessions"]],
			[s["inTime"] for s in from_timeline["sessions"]],
		)


if __name__ == "__main__":
	frappe.init(site="test")
	frappe.connect()
