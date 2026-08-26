"""What the tills board asks for, and what a shop that upgrades into it starts with.

`till_board` answers the half of the board that a Presence Till row cannot: who is
standing at each till right now, and what it has taken today. Both are already in
ERPNext, so neither costs the POS anything - the till never sends them and could not be
trusted with them if it did.

Two rules are load-bearing here and each has a test.

The first is scope. Every other presence endpoint derives its company from the thing
being asked about - an employee, a branch - because "a caller that can name its own scope
can name somebody else's". This one is asked about nothing, so it names no company at
all: it reads Presence Till through ordinary permissions and lets what comes back define
the scope. There is no argument to get wrong.

The second is that money is money. Only submitted invoices count, only today's, and a
refund comes off rather than adding on.

The last class covers the migration. Every shop already has Presence Till rows sitting at
Pending, created by tills that asked to join while the sweep was off, and switching this
feature on must not greet an existing customer with a page full of machines waiting for
permission. A till whose profile has really sold is grandfathered; nothing else is.
"""

from datetime import datetime, time

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime

from barakat.patches.approve_tills_that_have_already_sold import (
	execute as grandfather_tills,
)
from barakat.presence import api, watch
from barakat.presence.mode import MANUAL

BRANCH = "Board Presence Branch"
PROFILE = "Board Presence Profile"
QUIET_PROFILE = "Board Presence Profile Quiet"


class BoardFixtures(FrappeTestCase):
	"""Two tills at one branch: one that sells, one that never has."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.employee = frappe.get_all("Employee", pluck="name", limit=1)[0]
		cls.employee_name = frappe.db.get_value("Employee", cls.employee, "employee_name")
		for profile in (PROFILE, QUIET_PROFILE):
			cls._ensure_pos_profile(profile)

		if not frappe.db.exists("Branch", BRANCH):
			frappe.get_doc(
				{
					"doctype": "Branch",
					"branch": BRANCH,
					"custom_pos_company": cls.company,
					"custom_pos_profiles": [
						{"pos_profile": PROFILE},
						{"pos_profile": QUIET_PROFILE},
					],
				}
			).insert(ignore_links=True, ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._wipe()
		if frappe.db.exists("Branch", BRANCH):
			frappe.delete_doc("Branch", BRANCH, force=True, ignore_permissions=True)
		for profile in (PROFILE, QUIET_PROFILE):
			if frappe.db.exists("POS Profile", profile):
				frappe.delete_doc("POS Profile", profile, force=True, ignore_permissions=True)
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
		frappe.db.delete("POS Invoice", {"pos_profile": ("in", (PROFILE, QUIET_PROFILE))})
		frappe.db.delete(
			"POS Opening Entry", {"pos_profile": ("in", (PROFILE, QUIET_PROFILE))}
		)
		for till in frappe.get_all(
			"Presence Till",
			filters={"pos_profile": ("in", (PROFILE, QUIET_PROFILE))},
			pluck="name",
		):
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

	def _till(self, profile=PROFILE, status="Active"):
		if not frappe.db.exists("Presence Settings", {"custom_company": self.company}):
			frappe.get_doc(
				{
					"doctype": "Presence Settings",
					"custom_company": self.company,
					"mode": MANUAL,
				}
			).insert(ignore_permissions=True)
		api.request_join(profile, machine_name="DESK-BOARD", wants_heartbeat=True)
		till = frappe.db.exists("Presence Till", {"pos_profile": profile})
		frappe.db.set_value("Presence Till", till, "status", status)
		return till

	def _sale(
		self,
		total,
		profile=PROFILE,
		day=None,
		submitted=True,
		is_return=False,
		at=None,
	):
		"""A POS Invoice as a ROW, not as a transaction.

		Validation is bypassed and `docstatus` is set directly. What is under test is a
		sum over a table - building a real submitted invoice would need items, a
		warehouse, a price list and four accounts, and would be testing ERPNext.

		`at` sets the full stamp, because a shift owns its invoices by TIME rather than
		by a link: ERPNext has no field joining a POS Invoice to its opening entry, so
		both this and `barakat/api/shift.py` scope by profile plus the window that opened
		at `period_start_date`. A fixture that only set a date would sit at midnight and
		fall outside every shift that opened during the day.
		"""
		when = get_datetime(at) if at else None
		doc = frappe.get_doc(
			{
				"doctype": "POS Invoice",
				"company": self.company,
				"pos_profile": profile,
				"posting_date": when.date() if when else (day or getdate()),
				"posting_time": when.time() if when else None,
				"grand_total": total,
				"is_return": 1 if is_return else 0,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)
		if submitted:
			frappe.db.set_value("POS Invoice", doc.name, "docstatus", 1)
		return doc.name

	def _shift(self, profile=PROFILE, status="Open", employee=None, start=None):
		doc = frappe.get_doc(
			{
				"doctype": "POS Opening Entry",
				"company": self.company,
				"pos_profile": profile,
				"period_start_date": get_datetime(start) if start else now_datetime(),
				"custom_opened_by_staff": employee or self.employee,
				"status": status,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)
		frappe.db.set_value("POS Opening Entry", doc.name, {"docstatus": 1, "status": status})
		return doc.name

	def _for(self, board, profile=PROFILE):
		return next(row for row in board["tills"] if row["pos_profile"] == profile)


class TestTillBoard(BoardFixtures):
	def test_the_board_lists_this_shops_tills(self):
		self._till()

		board = api.till_board()

		self.assertIn(PROFILE, [row["pos_profile"] for row in board["tills"]])

	def test_the_board_names_who_is_on_the_till(self):
		self._till()
		self._shift()

		row = self._for(api.till_board())

		self.assertEqual(row["shift"]["cashier"], self.employee)
		self.assertEqual(row["shift"]["cashier_name"], self.employee_name)

	def test_an_open_shift_carries_its_own_takings(self):
		"""Not the same number as today's, and the difference is the point.

		A shift can start before midnight and a day can hold two of them, so "what this
		shift has taken" and "what this till has taken today" routinely disagree. The
		card shows the day; hovering asks about the shift the cashier is actually
		standing in, and answering that with the day's figure would be a quiet lie.
		"""
		self._till()
		noon = datetime.combine(getdate(), time(12, 0))
		self._shift(start=noon)
		self._sale(60, at=datetime.combine(getdate(), time(13, 0)))
		self._sale(40, at=datetime.combine(getdate(), time(14, 0)))
		# Today, and on this till, but rung up before this shift opened - the earlier
		# cashier's. It belongs to the day and not to the person standing there now.
		self._sale(25, at=datetime.combine(getdate(), time(9, 0)))

		row = self._for(api.till_board())

		self.assertEqual(row["shift"]["total"], 100)
		self.assertEqual(row["shift"]["invoices"], 2)
		self.assertEqual(row["today"]["total"], 125)

	def test_a_shift_that_has_sold_nothing_says_zero_rather_than_nothing(self):
		self._till()
		self._shift()

		row = self._for(api.till_board())

		self.assertEqual(row["shift"]["total"], 0)
		self.assertEqual(row["shift"]["invoices"], 0)

	def test_a_night_shift_keeps_what_it_rang_up_before_midnight(self):
		"""A shift owns everything since it opened, on both sides of midnight."""
		self._till()
		last_night = datetime.combine(add_to_date(getdate(), days=-1), time(22, 0))
		self._shift(start=last_night)
		self._sale(80, at=datetime.combine(add_to_date(getdate(), days=-1), time(23, 0)))

		row = self._for(api.till_board())

		self.assertEqual(row["shift"]["total"], 80)
		# ...and today's figure is still strictly today's.
		self.assertEqual(row["today"]["total"], 0)

	def test_a_till_nobody_has_opened_has_nobody_on_it(self):
		self._till()

		row = self._for(api.till_board())

		self.assertIsNone(row["shift"])

	def test_a_shift_that_was_closed_is_not_somebody_on_the_till(self):
		"""Otherwise last night's cashier stands at the till all night on screen."""
		self._till()
		self._shift(status="Closed")

		row = self._for(api.till_board())

		self.assertIsNone(row["shift"])

	def test_the_board_totals_what_the_till_took_today(self):
		self._till()
		self._sale(120)
		self._sale(80)

		row = self._for(api.till_board())

		self.assertEqual(row["today"]["total"], 200)
		self.assertEqual(row["today"]["invoices"], 2)

	def test_yesterdays_money_is_not_todays(self):
		self._till()
		self._sale(500, day=add_to_date(getdate(), days=-1))

		row = self._for(api.till_board())

		self.assertEqual(row["today"]["total"], 0)
		self.assertEqual(row["today"]["invoices"], 0)

	def test_a_refund_comes_off_the_days_total(self):
		self._till()
		self._sale(100)
		self._sale(-30, is_return=True)

		row = self._for(api.till_board())

		self.assertEqual(row["today"]["total"], 70)

	def test_an_unsubmitted_invoice_is_not_money(self):
		"""A draft on a cashier's screen is not a sale, and must not read as one."""
		self._till()
		self._sale(100, submitted=False)

		row = self._for(api.till_board())

		self.assertEqual(row["today"]["total"], 0)

	def test_one_tills_money_is_not_anothers(self):
		self._till()
		self._till(QUIET_PROFILE)
		self._sale(100, profile=PROFILE)

		board = api.till_board()

		self.assertEqual(self._for(board, PROFILE)["today"]["total"], 100)
		self.assertEqual(self._for(board, QUIET_PROFILE)["today"]["total"], 0)

	def test_looking_at_the_board_makes_the_tills_hurry(self):
		"""This is the whole live mechanism: being read is what speeds the shop up."""
		self._till()
		self.assertFalse(watch.is_watched(self.company))

		api.till_board()

		self.assertTrue(watch.is_watched(self.company))

	def test_the_board_says_how_long_the_hurrying_lasts(self):
		"""The page has to know how often to come back to hold the window open."""
		self._till()

		self.assertEqual(api.till_board()["watching_for_s"], watch.WATCH_TTL_S)

	def test_a_pending_till_is_on_the_board_too(self):
		"""A machine waiting for approval is exactly what somebody opens this to find."""
		self._till(status="Pending")

		row = self._for(api.till_board())

		self.assertEqual(row["pos_profile"], PROFILE)


class TestGrandfatheringExistingTills(BoardFixtures):
	def test_a_till_that_has_already_sold_is_let_in(self):
		till = self._till(status="Pending")
		self._sale(40)

		grandfather_tills()

		self.assertEqual(frappe.db.get_value("Presence Till", till, "status"), "Active")

	def test_a_till_that_never_sold_still_waits(self):
		till = self._till(QUIET_PROFILE, status="Pending")

		grandfather_tills()

		self.assertEqual(frappe.db.get_value("Presence Till", till, "status"), "Pending")

	def test_a_suspended_till_is_not_quietly_let_back_in(self):
		"""Somebody suspended it on purpose. A migration must not overrule them."""
		till = self._till(status="Suspended")
		self._sale(40)

		grandfather_tills()

		self.assertEqual(frappe.db.get_value("Presence Till", till, "status"), "Suspended")

	def test_a_till_approved_this_way_still_has_to_collect_a_key(self):
		"""Approval is not a key. The till asks, as it always did."""
		till = self._till(status="Pending")
		self._sale(40)

		grandfather_tills()

		self.assertIsNone(frappe.db.get_value("Presence Till", till, "key_issued_at"))

	def test_running_it_twice_changes_nothing_the_second_time(self):
		till = self._till(status="Pending")
		self._sale(40)
		grandfather_tills()
		frappe.db.set_value("Presence Till", till, "status", "Suspended")

		grandfather_tills()

		self.assertEqual(frappe.db.get_value("Presence Till", till, "status"), "Suspended")
