"""Taking a phone off the person it already belongs to.

Until this, a phone that scanned for a second person was simply moved: the first owner
was unpaired, their open shift was closed, and neither screen said a word. It is a
legitimate thing to do - phones get handed on when somebody leaves - but it is somebody
else's attendance, and it was happening on a scan by a staff member the manager had
merely pointed a QR at.

So the scan now only ASKS. Nothing is written until a manager answers on their own
screen, and this file is the proof of that in both directions: that a clash changes
nothing, and that everything the old silent path did still happens once the answer is
yes.

Most of what is in here is the second minute rather than the first. A manager reads a
dialog and thinks about it, and in that time the phone can be unpaired, handed to a third
person, or the branch's last till can be suspended - so every one of those is a test, not
a comment. The happy flow is two of the twenty-odd cases below.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from barakat.presence import api, keys, pairing, service
from barakat.presence.mode import MANUAL, WIFI

BRANCH = "Handover Presence Branch"
PROFILE = "Handover Presence Profile"
PHONE = "hand0deadbeef1"
SPARE = "hand0deadbeef2"


class TestPairingHandover(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		companies = frappe.get_all("Company", pluck="name", limit=2)
		cls.other_company = companies[1] if len(companies) > 1 else None

		employees = frappe.get_all(
			"Employee", filters={"company": cls.company}, pluck="name", limit=3
		) or frappe.get_all("Employee", pluck="name", limit=3)
		cls.new_owner = employees[0]
		cls.old_owner = employees[1] if len(employees) > 1 else employees[0]
		cls.third = employees[2] if len(employees) > 2 else cls.old_owner

		if not frappe.db.exists("POS Profile", PROFILE):
			doc = frappe.get_doc({"doctype": "POS Profile", "company": cls.company})
			doc.name = PROFILE
			doc.flags.ignore_validate = True
			doc.flags.ignore_mandatory = True
			doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)

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
	def _wipe(cls):
		for doctype in ("Presence Sighting", "Presence Live Device", "Presence Session"):
			frappe.db.delete(doctype, {"branch": BRANCH})
		frappe.db.delete("Employee Device", {"device_key": ("in", [PHONE, SPARE])})
		# The employees are borrowed from the site and may already carry pairings from
		# real use - and "have they any device left" is decided by exactly those. Left in
		# place, these tests pass or fail on somebody else's data.
		frappe.db.delete(
			"Employee Device",
			{"employee": ("in", [cls.new_owner, cls.old_owner, cls.third])},
		)
		frappe.db.delete("Presence Session", {"employee": ("in", [cls.new_owner, cls.old_owner, cls.third])})
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
		self.till_name = self._live_till()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ---------------------------------------------------------------- helpers

	def _live_till(self):
		api.request_join(PROFILE, machine_name="DESK-HANDOVER-01")
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
				"local_url": "http://192.168.1.9:7331",
			},
		)
		return till

	def _till(self):
		return frappe.get_doc("Presence Till", self.till_name)

	def _as_till(self):
		frappe.set_user(frappe.db.get_value("Presence Till", self.till_name, "api_user"))

	def _give(self, device_key, employee, company=None, valid_from="2020-01-01"):
		# `ignore_links` so a company that does not exist can be named. Only the
		# cross-tenant test uses that, and it uses it because a local site carries one
		# company - creating a second one would drag in a whole chart of accounts to
		# prove one `where` clause.
		frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": company or self.company,
				"employee": employee,
				"device_key": device_key,
				"valid_from": valid_from,
			}
		).insert(ignore_permissions=True, ignore_links=bool(company))

	def _scan(self, employee, device_key=PHONE):
		"""Start a window and have the till claim it. Returns (code, claim result)."""
		started = pairing.start(employee, BRANCH)
		self._as_till()
		result = pairing.claim(started["code"], device_key)
		frappe.set_user("Administrator")
		return started["code"], result

	def _open_rows(self, device_key=PHONE):
		return frappe.get_all(
			"Employee Device",
			filters={"device_key": device_key, "valid_to": ("is", "not set")},
			pluck="employee",
		)

	def _has_open_session(self, employee):
		return bool(
			frappe.db.exists(
				"Presence Session",
				{"employee": employee, "custom_company": self.company, "state": "Open"},
			)
		)

	def _state(self, code):
		return frappe.db.get_value("Presence Pairing Session", {"code": code}, "state")

	def _expire(self, code):
		frappe.db.set_value(
			"Presence Pairing Session",
			{"code": code},
			"expires_at",
			add_to_date(now_datetime(), seconds=-1),
		)

	# ------------------------------------------------- the scan changes nothing

	def test_a_scan_for_somebody_elses_phone_writes_nothing(self):
		"""The whole point. Not a refusal either - a question, with the answer pending."""
		self._give(PHONE, self.old_owner)
		code, result = self._scan(self.new_owner)

		self.assertFalse(result["ok"])
		self.assertTrue(result["needs_confirmation"])
		self.assertEqual(self._state(code), "Needs Confirmation")
		self.assertEqual(
			self._open_rows(),
			[self.old_owner],
			"the phone moved before anybody was asked",
		)

	def test_the_old_owner_keeps_their_shift_while_the_question_is_open(self):
		"""A pending question must not clock anybody out. They are still standing there."""
		self._give(PHONE, self.old_owner)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		self.assertTrue(self._has_open_session(self.old_owner), "no shift to lose")

		self._scan(self.new_owner)

		self.assertTrue(
			self._has_open_session(self.old_owner),
			"the old owner was sent home by a question nobody had answered",
		)

	def test_the_phone_is_never_told_whose_it_is(self):
		"""That page is reachable by anyone who can guess a URL on the shop wifi."""
		self._give(PHONE, self.old_owner)
		_, result = self._scan(self.new_owner)

		message = result["message"]
		name = frappe.db.get_value("Employee", self.old_owner, "employee_name") or ""
		self.assertNotIn(self.old_owner, message)
		if name:
			self.assertNotIn(name, message)

	def test_the_scan_restarts_the_clock(self):
		"""What ran out was the time to scan, and the scan has happened.

		Inheriting the tail of the first window would put a dialog on screen that expires
		while it is being read.
		"""
		self._give(PHONE, self.old_owner)
		started = pairing.start(self.new_owner, BRANCH)
		first = frappe.db.get_value("Presence Pairing Session", {"code": started["code"]}, "expires_at")
		self._as_till()
		pairing.claim(started["code"], PHONE)
		frappe.set_user("Administrator")

		second = frappe.db.get_value("Presence Pairing Session", {"code": started["code"]}, "expires_at")
		self.assertGreater(second, first, "the manager inherited the scanner's clock")

	# --------------------------------------------------- what the manager is told

	def test_the_manager_is_told_who_holds_it_by_name(self):
		"""`HR-EMP-00374` is not a person to anybody standing at a till."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		conflict = pairing.status(code)["conflict"]
		self.assertEqual(conflict["employee"], self.old_owner)
		self.assertEqual(
			conflict["employeeName"],
			frappe.db.get_value("Employee", self.old_owner, "employee_name"),
		)

	def test_the_manager_is_told_that_it_ends_a_shift(self):
		"""The half that decides it. Confirming sends the old owner home in the record."""
		self._give(PHONE, self.old_owner)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		code, _ = self._scan(self.new_owner)

		self.assertTrue(pairing.status(code)["conflict"]["onTheClock"])

	def test_a_clash_with_nobody_on_the_clock_says_so(self):
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		self.assertFalse(pairing.status(code)["conflict"]["onTheClock"])

	def test_an_ordinary_pairing_carries_no_conflict(self):
		code, result = self._scan(self.new_owner)
		self.assertTrue(result["ok"])
		self.assertIsNone(pairing.status(code)["conflict"])

	def test_the_countdown_comes_from_the_server(self):
		"""The browser's clock is not the site's, and `confirm` refuses on the site's."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		self.assertGreater(pairing.status(code)["expiresInS"], 0)

	# ------------------------------------------------------------------ yes

	def test_confirming_moves_the_phone_and_keeps_the_old_row(self):
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		pairing.confirm(code)

		self.assertEqual(self._open_rows(), [self.new_owner])
		self.assertTrue(
			frappe.db.exists("Employee Device", {"device_key": PHONE, "employee": self.old_owner}),
			"the old pairing was deleted instead of closed",
		)
		self.assertEqual(self._state(code), "Claimed")

	def test_confirming_closes_the_old_owners_shift(self):
		"""Everything the silent path did, still done - once somebody has said yes."""
		self._give(PHONE, self.old_owner)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		code, _ = self._scan(self.new_owner)

		pairing.confirm(code)

		self.assertFalse(
			self._has_open_session(self.old_owner),
			"the old owner was left on a shift kept alive by a phone that is not theirs",
		)

	def test_confirming_opens_the_new_owners_shift(self):
		"""The phone is in the room - it just scanned. That is somebody at work."""
		self._give(PHONE, self.old_owner)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		code, _ = self._scan(self.new_owner)

		pairing.confirm(code)

		self.assertTrue(self._has_open_session(self.new_owner))

	def test_a_takeover_is_written_down(self):
		"""A closed pairing beside an open one reads as a mystery six months later."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)
		pairing.confirm(code)

		note = frappe.db.get_value(
			"Employee Device",
			{"device_key": PHONE, "employee": self.old_owner},
			"notes",
		)
		self.assertIn(self.new_owner, note or "")

	def test_the_window_remembers_who_was_asked_about(self):
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)
		pairing.confirm(code)

		self.assertEqual(
			frappe.db.get_value("Presence Pairing Session", {"code": code}, "conflict_employee"),
			self.old_owner,
		)

	# ------------------------------------------------------------------- no

	def test_cancelling_leaves_everything_exactly_as_it_was(self):
		self._give(PHONE, self.old_owner)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		code, _ = self._scan(self.new_owner)

		pairing.cancel(code)

		self.assertEqual(self._state(code), "Cancelled")
		self.assertEqual(self._open_rows(), [self.old_owner])
		self.assertTrue(self._has_open_session(self.old_owner))

	def test_a_cancelled_window_still_says_who_it_was_about(self):
		"""The screen that reports the refusal names the person it left the phone with.

		Found live, not in a test: `conflict` used to be filled in only while the question
		was open, so the moment it was answered the manager's screen said "nothing
		changed, that phone still counts for ." with the name missing.
		"""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)
		pairing.cancel(code)

		answered = pairing.status(code)
		self.assertEqual(answered["state"], "Cancelled")
		self.assertEqual(answered["conflict"]["employee"], self.old_owner)

	def test_a_cancelled_window_cannot_then_be_confirmed(self):
		"""Two minutes of a live code after somebody has said no is two minutes too many."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)
		pairing.cancel(code)

		with self.assertRaises(frappe.ValidationError):
			pairing.confirm(code)

	def test_a_window_cannot_be_confirmed_twice(self):
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)
		pairing.confirm(code)

		with self.assertRaises(frappe.ValidationError):
			pairing.confirm(code)

	# ------------------------------------------------------- the second minute

	def test_an_expired_window_is_refused(self):
		"""A scan from last week, answered against a room nobody can see."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)
		self._expire(code)

		with self.assertRaises(frappe.ValidationError):
			pairing.confirm(code)
		self.assertEqual(self._open_rows(), [self.old_owner])

	def test_status_times_a_held_window_out(self):
		"""Nothing else would. `Waiting` was the only state that ever expired itself."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)
		self._expire(code)

		self.assertEqual(pairing.status(code)["state"], "Expired")
		self.assertEqual(self._state(code), "Expired")

	def test_a_phone_that_changed_hands_under_the_manager_is_refused(self):
		"""They answered a dialog naming somebody who no longer holds it."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		# A second manager, elsewhere, moves the same phone to a third person.
		pairing._pair(self.third, PHONE, self.company)

		with self.assertRaises(frappe.ValidationError):
			pairing.confirm(code)
		self.assertEqual(self._open_rows(), [self.third], "an answer about the wrong person was applied")

	def test_a_phone_unpaired_under_the_manager_still_pairs(self):
		"""The clash resolved itself. A takeover of nobody is an ordinary pairing."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		name = frappe.db.get_value(
			"Employee Device", {"device_key": PHONE, "employee": self.old_owner}, "name"
		)
		pairing.unpair(name)

		pairing.confirm(code)
		self.assertEqual(self._open_rows(), [self.new_owner])

	def test_confirming_what_somebody_else_already_did_is_a_no_op(self):
		"""Two managers, one phone. The end state asked for is the end state we are in."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		pairing._pair(self.new_owner, PHONE, self.company)

		result = pairing.confirm(code)
		self.assertTrue(result["already"])
		self.assertEqual(self._open_rows(), [self.new_owner])
		self.assertEqual(
			frappe.db.count("Employee Device", {"device_key": PHONE, "valid_to": ("is", "not set")}),
			1,
			"a duplicate open pairing was created",
		)

	def test_no_shift_is_opened_when_the_branch_has_lost_its_last_till(self):
		"""Suspending the last till closes every shift there on purpose.

		Opening a fresh one behind that leaves a session with nothing in the building
		able to close it - somebody at work for ever.
		"""
		self._give(PHONE, self.old_owner)
		service.ingest(self._till(), [PHONE], now_datetime(), settled=True)
		code, _ = self._scan(self.new_owner)

		api.suspend(self.till_name)
		pairing.confirm(code)

		self.assertEqual(self._open_rows(), [self.new_owner], "the pairing itself should stand")
		self.assertFalse(
			self._has_open_session(self.new_owner),
			"a shift was opened at a branch with nothing left watching it",
		)

	def test_wifi_switched_off_between_the_scan_and_the_answer_is_refused(self):
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		frappe.db.set_value("Presence Settings", {"custom_company": self.company}, "mode", MANUAL)

		with self.assertRaises(frappe.ValidationError):
			pairing.confirm(code)

	def test_a_new_code_kills_a_window_waiting_on_an_answer(self):
		"""Otherwise a takeover somebody walked away from is confirmable minutes later."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		pairing.start(self.new_owner, BRANCH)

		self.assertFalse(
			frappe.db.exists("Presence Pairing Session", {"code": code}),
			"a window awaiting an answer survived being replaced",
		)

	# ------------------------------------------------------------- who may answer

	def test_a_till_may_not_confirm(self):
		"""A stolen till key must not be able to walk a phone off one person onto another."""
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		self._as_till()
		with self.assertRaises(frappe.PermissionError):
			pairing.confirm(code)
		frappe.set_user("Administrator")
		self.assertEqual(self._open_rows(), [self.old_owner])

	def test_a_till_may_not_cancel_either(self):
		self._give(PHONE, self.old_owner)
		code, _ = self._scan(self.new_owner)

		self._as_till()
		with self.assertRaises(frappe.PermissionError):
			pairing.cancel(code)
		frappe.set_user("Administrator")
		self.assertEqual(self._state(code), "Needs Confirmation")

	def test_an_unknown_code_cannot_be_confirmed(self):
		with self.assertRaises(frappe.DoesNotExistError):
			pairing.confirm("nosuchcode456")

	def test_a_window_nobody_has_scanned_cannot_be_confirmed(self):
		"""There is no device to move yet, so there is nothing to say yes to."""
		started = pairing.start(self.new_owner, BRANCH)
		with self.assertRaises(frappe.ValidationError):
			pairing.confirm(started["code"])

	# --------------------------------------------------- when NOT to ask at all

	def test_a_persons_own_phone_never_raises_the_question(self):
		"""The common accident - scanning twice for one person - must stay a no-op."""
		self._give(PHONE, self.new_owner)
		code, result = self._scan(self.new_owner)

		self.assertTrue(result["ok"])
		self.assertEqual(self._state(code), "Claimed")
		self.assertEqual(
			frappe.db.count("Employee Device", {"device_key": PHONE, "valid_to": ("is", "not set")}),
			1,
		)

	def test_a_closed_pairing_does_not_raise_the_question(self):
		"""History must never stand in the way of a new owner."""
		self._give(PHONE, self.old_owner)
		name = frappe.db.get_value(
			"Employee Device", {"device_key": PHONE, "employee": self.old_owner}, "name"
		)
		pairing.unpair(name)

		code, result = self._scan(self.new_owner)

		self.assertTrue(result["ok"], "a pairing that had already ended blocked a new one")
		self.assertEqual(self._state(code), "Claimed")

	def test_another_companys_phone_is_not_this_shops_business(self):
		"""Naming their staff here would be the Contact and Item Price leak again."""
		self._give(PHONE, self.old_owner, company=self.other_company or "Another Tenant")
		code, result = self._scan(self.new_owner)

		self.assertTrue(result["ok"], "a device paired at another tenant blocked a pairing")
		self.assertIsNone(pairing.status(code)["conflict"])

	def test_a_second_phone_for_the_same_person_never_raises_the_question(self):
		"""Somebody carrying a phone and a tablet is not taking anything off anybody."""
		self._give(PHONE, self.new_owner)
		_code, result = self._scan(self.new_owner, device_key=SPARE)

		self.assertTrue(result["ok"])
		self.assertEqual(
			sorted(self._open_rows(PHONE) + self._open_rows(SPARE)),
			sorted([self.new_owner, self.new_owner]),
		)

	def test_the_till_guard_is_not_leaning_on_the_permission_check(self):
		"""Both refuse a till, and they refuse it for different reasons.

		`till_for_current_user` is the one that means it: the day somebody widens what a
		till account may write, the permission check stops being the thing standing here.
		"""
		self._as_till()
		self.assertIsNotNone(keys.till_for_current_user())
		frappe.set_user("Administrator")
