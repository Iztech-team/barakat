"""The presence engine, tested as pure functions.

No frappe, no site, no database, no real clock. Every test is a timeline in and a
list of decisions out. This is the whole reason the engine is Frappe-free: the hard
part of this feature is decided here, so it has to be cheap to test exhaustively.
"""

import unittest
from datetime import datetime, timedelta

from barakat.presence.engine import (
	Decision,
	ARRIVED,
	DEPARTED,
	BranchState,
	Report,
	abandoned,
	apply_report,
	last_contact,
	tick,
)

T0 = datetime(2026, 8, 11, 8, 0, 0)


def at(seconds):
	"""Seconds after T0, so tests read as a timeline."""
	return T0 + timedelta(seconds=seconds)


class TestArrivals(unittest.TestCase):
	def test_first_sighting_of_a_device_is_an_arrival(self):
		state = BranchState()

		decisions = apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, ARRIVED)
		self.assertEqual(decisions[0].device_key, "dev-a")
		self.assertEqual(decisions[0].at, at(0))

	def test_seeing_the_same_device_again_is_not_a_second_arrival(self):
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))

		decisions = apply_report(state, Report("till-1", at(2), frozenset({"dev-a"})))

		self.assertEqual(decisions, [])

	def test_two_devices_in_one_report_produce_two_arrivals(self):
		state = BranchState()

		decisions = apply_report(
			state, Report("till-1", at(0), frozenset({"dev-a", "dev-b"}))
		)

		self.assertEqual({d.device_key for d in decisions}, {"dev-a", "dev-b"})
		self.assertTrue(all(d.kind == ARRIVED for d in decisions))

	def test_a_device_seen_by_a_second_till_is_not_a_second_arrival(self):
		"""The branch is the unit of truth, not the till (spec 8.1)."""
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))

		decisions = apply_report(state, Report("till-2", at(1), frozenset({"dev-a"})))

		self.assertEqual(decisions, [])

	def test_an_older_report_does_not_move_last_seen_backwards(self):
		"""Out-of-order arrival must not make a device look staler than it is."""
		state = BranchState()
		apply_report(state, Report("till-1", at(10), frozenset({"dev-a"})))

		apply_report(state, Report("till-2", at(4), frozenset({"dev-a"})))

		self.assertEqual(state.last_seen["dev-a"], at(10))


class TestDepartures(unittest.TestCase):
	"""Departures, the wait, and merging several tills into one branch answer.

	Every test keeps a watcher reporting right up to the moment of the `tick`. That is
	not decoration: `tick` refuses to age anything out when no watcher has reported
	recently, so a test whose last report is stale would pass with an empty result no
	matter what the wait logic did. Keeping the branch covered is what makes these
	tests actually about departures.
	"""

	WAIT = timedelta(minutes=15)
	STALE = timedelta(minutes=5)

	def _seen(self, state, when, *devices, till="till-1"):
		return apply_report(state, Report(till, when, frozenset(devices)))

	def _present(self, *devices):
		state = BranchState()
		self._seen(state, at(0), *devices)
		return state

	def test_no_departure_before_the_wait_expires(self):
		state = self._present("dev-a")
		self._seen(state, at(14 * 60))

		self.assertEqual(tick(state, at(14 * 60), self.WAIT, self.STALE), [])

	def test_departure_once_the_wait_expires(self):
		state = self._present("dev-a")
		self._seen(state, at(16 * 60))

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, DEPARTED)
		self.assertEqual(decisions[0].device_key, "dev-a")

	def test_departure_time_is_when_the_device_vanished_not_when_the_timer_finished(self):
		"""Spec 8.3: out_time is the real disappearance, not the timer expiry."""
		state = self._present("dev-a")
		self._seen(state, at(16 * 60))

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(decisions[0].at, at(0))

	def test_a_device_that_comes_back_in_time_produces_no_event_at_all(self):
		"""The pocket-sleep case. Not a departure, and not a second arrival."""
		state = self._present("dev-a")

		self._seen(state, at(10 * 60))
		gone = tick(state, at(10 * 60), self.WAIT, self.STALE)

		back = self._seen(state, at(11 * 60), "dev-a")

		self._seen(state, at(20 * 60), "dev-a")
		later = tick(state, at(20 * 60), self.WAIT, self.STALE)

		self.assertEqual(gone, [])
		self.assertEqual(back, [])
		self.assertEqual(later, [])

	def test_flapping_twenty_times_still_produces_no_event(self):
		state = self._present("dev-a")

		for minute in range(1, 41, 2):
			self._seen(state, at(minute * 60))
			self.assertEqual(tick(state, at(minute * 60), self.WAIT, self.STALE), [])
			self._seen(state, at((minute + 1) * 60), "dev-a")

		self.assertIn("dev-a", state.present)

	def test_departing_then_returning_is_a_fresh_arrival(self):
		state = self._present("dev-a")
		self._seen(state, at(16 * 60))
		tick(state, at(16 * 60), self.WAIT, self.STALE)

		decisions = self._seen(state, at(17 * 60), "dev-a")

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, ARRIVED)

	def test_one_till_still_seeing_the_device_keeps_it_present(self):
		"""Present if ANY till sees it. Not a vote, not an average."""
		state = self._present("dev-a")
		self._seen(state, at(14 * 60), "dev-a", till="till-2")
		self._seen(state, at(16 * 60))

		self.assertEqual(tick(state, at(16 * 60), self.WAIT, self.STALE), [])

	def test_departure_only_once_every_till_has_lost_it(self):
		state = self._present("dev-a")
		self._seen(state, at(60), "dev-a", till="till-2")
		self._seen(state, at(120))
		self._seen(state, at(120), till="till-2")
		self._seen(state, at(17 * 60))

		decisions = tick(state, at(17 * 60), self.WAIT, self.STALE)

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].at, at(60))


class TestCoverage(unittest.TestCase):
	"""The two ways a branch stops being trustworthy, and neither may look like empty.

	This is the difference between a system that fails loudly and one that lies
	quietly. A branch nobody can see is unreachable; recording it as "nobody came to
	work" would be a wrong answer delivered with total confidence, and it would not be
	noticed until payday.
	"""

	WAIT = timedelta(minutes=15)
	STALE = timedelta(minutes=5)

	def test_a_warming_up_watcher_cannot_cause_departures(self):
		"""A till that just rebooted knows nothing. Its empty view is not evidence."""
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))
		apply_report(state, Report("till-1", at(16 * 60), frozenset(), settled=False))

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(decisions, [])
		self.assertIn("dev-a", state.present)

	def test_a_settled_watcher_elsewhere_still_allows_departures(self):
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))
		apply_report(state, Report("till-1", at(16 * 60), frozenset(), settled=False))
		apply_report(state, Report("till-2", at(16 * 60), frozenset()))

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, DEPARTED)

	def test_a_silent_branch_cannot_cause_departures(self):
		"""Power cut, dead internet, crashed watcher. Unreachable, never empty."""
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))

		decisions = tick(state, at(60 * 60), self.WAIT, self.STALE)

		self.assertEqual(decisions, [])
		self.assertIn("dev-a", state.present)

	def test_a_branch_with_no_watchers_at_all_cannot_cause_departures(self):
		state = BranchState()
		state.last_seen["dev-a"] = at(0)
		state.present.add("dev-a")

		decisions = tick(state, at(60 * 60), self.WAIT, self.STALE)

		self.assertEqual(decisions, [])

	def test_an_empty_shop_with_a_healthy_watcher_does_produce_departures(self):
		"""The control. Coverage must not suppress the normal case."""
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))
		apply_report(state, Report("till-1", at(16 * 60), frozenset()))

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(len(decisions), 1)


class EngineCase(unittest.TestCase):
	"""Shared helpers. `seen` keeps the branch covered so `tick` is allowed to act."""

	WAIT = timedelta(minutes=15)
	STALE = timedelta(minutes=5)

	def seen(self, state, when, *devices, till="till-1", settled=True):
		return apply_report(
			state, Report(till, when, frozenset(devices), settled=settled)
		)

	def tick(self, state, when, wait=None, stale=None):
		# `is None`, not `or`: timedelta(0) is falsy, so `wait or self.WAIT` would
		# silently substitute 15 minutes for a deliberate zero and the degenerate
		# tests below would pass without testing anything. Same falsy-zero trap that
		# bit `settings_for` in mode.py.
		return tick(
			state,
			when,
			self.WAIT if wait is None else wait,
			self.STALE if stale is None else stale,
		)


class TestBoundaries(EngineCase):
	"""Off-by-one at the wait boundary decides whether somebody is paid for a shift."""

	def test_exactly_at_the_wait_is_not_yet_a_departure(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(15 * 60))

		self.assertEqual(self.tick(state, at(15 * 60)), [])

	def test_one_second_past_the_wait_is_a_departure(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(15 * 60 + 1))

		self.assertEqual(len(self.tick(state, at(15 * 60 + 1))), 1)

	def test_exactly_at_stale_still_counts_as_covered(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")

		self.assertEqual(len(self.tick(state, at(16 * 60), stale=timedelta(minutes=16))), 1)

	def test_an_empty_report_is_valid_and_changes_nothing(self):
		state = BranchState()

		decisions = self.seen(state, at(0))

		self.assertEqual(decisions, [])
		self.assertEqual(state.present, set())

	def test_a_full_report_at_the_cap_is_handled(self):
		"""512 devices is the spec's per-report cap. A busy shop really does hit it."""
		state = BranchState()
		devices = [f"dev-{i:04d}" for i in range(512)]

		decisions = self.seen(state, at(0), *devices)

		self.assertEqual(len(decisions), 512)
		self.assertEqual(len(state.present), 512)

	def test_every_one_of_a_large_set_departs_together(self):
		state = BranchState()
		devices = [f"dev-{i:04d}" for i in range(512)]
		self.seen(state, at(0), *devices)
		self.seen(state, at(20 * 60))

		decisions = self.tick(state, at(20 * 60))

		self.assertEqual(len(decisions), 512)
		self.assertEqual(state.present, set())


class TestIdempotenceAndOrdering(EngineCase):
	"""Retries and out-of-order delivery must not invent or lose events."""

	def test_the_same_report_applied_twice_arrives_once(self):
		state = BranchState()
		report = Report("till-1", at(0), frozenset({"dev-a"}))

		first = apply_report(state, report)
		second = apply_report(state, report)

		self.assertEqual(len(first), 1)
		self.assertEqual(second, [])

	def test_a_stale_report_cannot_resurrect_a_departed_device(self):
		"""A queued report from before the departure must not reopen the session."""
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(20 * 60))
		self.assertEqual(len(self.tick(state, at(20 * 60))), 1)

		late = self.seen(state, at(5 * 60), "dev-a", till="till-2")

		self.assertEqual(len(late), 1, "a re-sighting is an arrival, not a no-op")
		self.assertEqual(late[0].kind, ARRIVED)

	def test_two_tills_reporting_out_of_order_keep_the_newest_sighting(self):
		state = BranchState()
		self.seen(state, at(600), "dev-a", till="till-1")
		self.seen(state, at(100), "dev-a", till="till-2")

		self.assertEqual(state.last_seen["dev-a"], at(600))

	def test_ticking_twice_does_not_depart_twice(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(20 * 60))

		first = self.tick(state, at(20 * 60))
		second = self.tick(state, at(21 * 60))

		self.assertEqual(len(first), 1)
		self.assertEqual(second, [])

	def test_a_tick_with_nothing_present_is_harmless(self):
		state = BranchState()
		self.seen(state, at(0))

		self.assertEqual(self.tick(state, at(60 * 60)), [])


class TestOfflineReplay(EngineCase):
	"""Shop internet drops; the watcher queues and replays. Nothing may be lost."""

	def test_a_queued_burst_replayed_in_order_produces_one_arrival(self):
		state = BranchState()

		decisions = []
		for minute in range(0, 30):
			decisions += self.seen(state, at(minute * 60), "dev-a")

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, ARRIVED)

	def test_a_gap_inside_a_replayed_burst_still_produces_a_departure(self):
		"""The person really did leave during the outage. The queue proves it."""
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		for minute in range(20, 40):
			self.seen(state, at(minute * 60))

		decisions = self.tick(state, at(40 * 60))

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].at, at(0))

	def test_a_replay_that_ends_with_the_device_back_produces_no_departure(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(5 * 60))
		self.seen(state, at(10 * 60), "dev-a")
		self.seen(state, at(15 * 60), "dev-a")

		self.assertEqual(self.tick(state, at(15 * 60)), [])


class TestManyTills(EngineCase):
	"""Three watchers in one room. The branch answers, never a single till."""

	def test_three_tills_seeing_the_same_device_arrive_it_once(self):
		state = BranchState()

		a = self.seen(state, at(0), "dev-a", till="till-1")
		b = self.seen(state, at(1), "dev-a", till="till-2")
		c = self.seen(state, at(2), "dev-a", till="till-3")

		self.assertEqual(len(a) + len(b) + len(c), 1)

	def test_two_tills_going_offline_do_not_end_anybodys_day(self):
		state = BranchState()
		for till in ("till-1", "till-2", "till-3"):
			self.seen(state, at(0), "dev-a", till=till)

		self.seen(state, at(30 * 60), "dev-a", till="till-3")

		self.assertEqual(self.tick(state, at(30 * 60)), [])
		self.assertIn("dev-a", state.present)

	def test_the_last_surviving_till_going_quiet_suppresses_departures(self):
		"""All three gone means unreachable, and unreachable is never empty."""
		state = BranchState()
		for till in ("till-1", "till-2", "till-3"):
			self.seen(state, at(0), "dev-a", till=till)

		self.assertEqual(self.tick(state, at(60 * 60)), [])
		self.assertIn("dev-a", state.present)

	def test_one_healthy_till_among_three_stale_ones_is_enough(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a", till="till-1")
		self.seen(state, at(0), "dev-a", till="till-2")
		self.seen(state, at(30 * 60), till="till-3")

		decisions = self.tick(state, at(30 * 60))

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].at, at(0))


class TestSeveralBranches(EngineCase):
	"""One state per branch. They must not leak into each other."""

	def test_two_branches_track_the_same_device_independently(self):
		ramallah = BranchState()
		nablus = BranchState()

		self.seen(ramallah, at(0), "dev-a")
		self.seen(nablus, at(0), "dev-b")

		self.assertEqual(ramallah.present, {"dev-a"})
		self.assertEqual(nablus.present, {"dev-b"})

	def test_a_device_moving_between_branches_departs_one_and_arrives_at_the_other(self):
		ramallah = BranchState()
		nablus = BranchState()
		self.seen(ramallah, at(0), "dev-a")

		arrived = self.seen(nablus, at(40 * 60), "dev-a")
		self.seen(ramallah, at(40 * 60))
		departed = self.tick(ramallah, at(40 * 60))

		self.assertEqual(arrived[0].kind, ARRIVED)
		self.assertEqual(departed[0].kind, DEPARTED)
		self.assertEqual(departed[0].at, at(0))

	def test_one_branch_going_dark_does_not_affect_another(self):
		ramallah = BranchState()
		nablus = BranchState()
		self.seen(ramallah, at(0), "dev-a")
		self.seen(nablus, at(0), "dev-b")
		self.seen(nablus, at(20 * 60))

		self.assertEqual(self.tick(ramallah, at(20 * 60)), [])
		self.assertEqual(len(self.tick(nablus, at(20 * 60))), 1)


class TestAFullDay(EngineCase):
	"""Realistic shifts end to end. These are the ones a shop owner would recognise."""

	def test_a_normal_shift_produces_exactly_one_arrival_and_one_departure(self):
		state = BranchState()
		events = []

		# 08:00 arrives, seen every 2 minutes until 17:00, with pocket sleeps.
		events += self.seen(state, at(0), "dev-a")
		for minute in range(2, 540, 2):
			present = minute % 20 != 0  # drops off briefly every 20 minutes
			if present:
				events += self.seen(state, at(minute * 60), "dev-a")
			else:
				events += self.seen(state, at(minute * 60))
			events += self.tick(state, at(minute * 60))

		# 17:00 leaves for good.
		for minute in range(540, 580, 2):
			events += self.seen(state, at(minute * 60))
			events += self.tick(state, at(minute * 60))

		arrivals = [e for e in events if e.kind == ARRIVED]
		departures = [e for e in events if e.kind == DEPARTED]

		self.assertEqual(len(arrivals), 1, "one arrival for one shift")
		self.assertEqual(len(departures), 1, "one departure for one shift")
		self.assertEqual(arrivals[0].at, at(0))
		self.assertEqual(departures[0].at, at(538 * 60))

	def test_a_split_shift_produces_two_sessions(self):
		"""Morning, a real two-hour gap, then back. That is genuinely two sessions."""
		state = BranchState()
		events = []

		events += self.seen(state, at(0), "dev-a")
		for minute in range(2, 240, 2):
			events += self.seen(state, at(minute * 60))
			events += self.tick(state, at(minute * 60))

		events += self.seen(state, at(240 * 60), "dev-a")
		for minute in range(242, 480, 2):
			events += self.seen(state, at(minute * 60))
			events += self.tick(state, at(minute * 60))

		self.assertEqual(len([e for e in events if e.kind == ARRIVED]), 2)
		self.assertEqual(len([e for e in events if e.kind == DEPARTED]), 2)

	def test_a_shift_crossing_midnight_is_not_cut_in_two(self):
		"""Sessions are not split at midnight. Real timestamps, one session."""
		state = BranchState()
		start = 15 * 3600  # 23:00
		self.seen(state, at(start), "dev-a")
		for offset in range(120, 7200, 120):
			self.seen(state, at(start + offset), "dev-a")

		self.seen(state, at(start + 7200 + 20 * 60))
		decisions = self.tick(state, at(start + 7200 + 20 * 60))

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].at, at(start + 7080))

	def test_a_whole_team_arriving_and_leaving_is_tracked_per_person(self):
		state = BranchState()
		team = [f"dev-{i}" for i in range(8)]

		arrivals = []
		for index, device in enumerate(team):
			arrivals += self.seen(state, at(index * 300), device)

		for index, device in enumerate(team):
			still_here = team[index + 1 :]
			self.seen(state, at(3600 + index * 300), *still_here)

		self.seen(state, at(3600 + 8 * 300 + 20 * 60))
		departures = self.tick(state, at(3600 + 8 * 300 + 20 * 60))

		self.assertEqual(len(arrivals), 8)
		self.assertEqual(len(departures), 8)
		self.assertEqual(state.present, set())


class TestDegenerateSettings(EngineCase):
	"""Nonsense numbers must not crash. The settings layer guards them; this proves
	the engine does not blow up if one ever gets through."""

	def test_a_zero_wait_departs_on_the_next_tick(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(1))

		decisions = self.tick(state, at(1), wait=timedelta(0))

		self.assertEqual(len(decisions), 1)

	def test_a_zero_stale_window_only_trusts_a_report_from_this_exact_instant(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")

		# One second later the only report is already stale, so nothing may age out.
		self.assertEqual(self.tick(state, at(1), stale=timedelta(0)), [])

		# At the exact instant of the report the branch is covered - but the device was
		# also seen at that instant, so it has been missing for zero seconds and zero is
		# not more than a zero wait. Still present, correctly.
		self.assertEqual(
			self.tick(state, at(0), wait=timedelta(0), stale=timedelta(0)), []
		)
		self.assertIn("dev-a", state.present)

	def test_a_zero_wait_departs_a_device_missing_for_one_second(self):
		"""`>` not `>=`: a device seen at this instant has been gone for no time."""
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(1))

		self.assertEqual(len(self.tick(state, at(1), wait=timedelta(0))), 1)

	def test_a_huge_wait_never_departs(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a")
		self.seen(state, at(86400))

		self.assertEqual(self.tick(state, at(86400), wait=timedelta(days=365)), [])

	def test_a_till_that_never_settles_can_never_cause_a_departure(self):
		state = BranchState()
		self.seen(state, at(0), "dev-a", settled=False)
		for minute in range(1, 60):
			self.seen(state, at(minute * 60), settled=False)

		self.assertEqual(self.tick(state, at(60 * 60)), [])


class TestValueShape(EngineCase):
	"""Decisions and reports are values. Nothing downstream may mutate them."""

	def test_a_decision_cannot_be_modified(self):
		decision = Decision(ARRIVED, "dev-a", at(0))

		with self.assertRaises(Exception):
			decision.kind = DEPARTED

	def test_a_report_cannot_be_modified(self):
		report = Report("till-1", at(0), frozenset({"dev-a"}))

		with self.assertRaises(Exception):
			report.till = "till-2"

	def test_applying_a_report_does_not_mutate_it(self):
		state = BranchState()
		report = Report("till-1", at(0), frozenset({"dev-a"}))

		apply_report(state, report)

		self.assertEqual(report.devices, frozenset({"dev-a"}))
		self.assertEqual(report.at, at(0))

	def test_a_fresh_branch_state_shares_nothing_with_another(self):
		"""A mutable default would make every branch the same branch."""
		first = BranchState()
		second = BranchState()

		first.present.add("dev-a")
		first.last_seen["dev-a"] = at(0)
		first.till_last_report["till-1"] = (at(0), True)

		self.assertEqual(second.present, set())
		self.assertEqual(second.last_seen, {})
		self.assertEqual(second.till_last_report, {})


class TestAbandonedBranch(unittest.TestCase):
	"""A branch whose tills stopped reporting at all.

	`tick` refuses to act here on purpose — one till losing power must not send a shop
	home. The cost of that refusal is that nothing ever closes those shifts, so every
	evening the last till is switched off and the whole shop stays "at work" for ever.
	This is the other half of the rule.
	"""

	DARK = timedelta(minutes=45)

	def _shop(self, *, till_at, device_at):
		"""One device present, one till, both stamped where the test wants them."""
		state = BranchState()
		apply_report(state, Report("till-1", device_at, frozenset({"dev-a"})))
		state.till_last_report["till-1"] = (till_at, True)
		return state

	def test_a_branch_dark_past_the_window_writes_everyone_off(self):
		state = self._shop(till_at=at(0), device_at=at(0))

		decisions = abandoned(state, at(60 * 60), self.DARK)

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, DEPARTED)
		self.assertEqual(decisions[0].device_key, "dev-a")

	def test_the_departure_is_dated_to_the_last_evidence_not_to_now(self):
		"""The whole reason a generous window costs nothing.

		Noticing at 09:00 that a till died at 08:00 must not record an hour of work
		nobody did.
		"""
		state = self._shop(till_at=at(0), device_at=at(0))

		decisions = abandoned(state, at(60 * 60), self.DARK)

		self.assertEqual(decisions[0].at, at(0))

	def test_the_earlier_of_the_two_evidences_wins(self):
		# The till kept reporting for another ten minutes, but it stopped SEEING this
		# device at minute two. Two is when we last knew.
		state = self._shop(till_at=at(600), device_at=at(120))

		decisions = abandoned(state, at(3 * 60 * 60), self.DARK)

		self.assertEqual(decisions[0].at, at(120))

	def test_a_till_that_went_quiet_a_moment_ago_is_left_alone(self):
		# A restart, an update, a ten-minute internet drop. Not a closed shop.
		state = self._shop(till_at=at(0), device_at=at(0))

		self.assertEqual(abandoned(state, at(10 * 60), self.DARK), [])

	def test_exactly_at_the_window_is_not_yet_dark(self):
		state = self._shop(till_at=at(0), device_at=at(0))

		self.assertEqual(abandoned(state, at(45 * 60), self.DARK), [])

	def test_a_branch_that_never_reported_writes_nobody_off(self):
		# Nothing to conclude from a branch we have never heard from.
		state = BranchState()

		self.assertEqual(abandoned(state, at(60 * 60), self.DARK), [])

	def test_an_empty_shop_going_dark_produces_nothing(self):
		state = BranchState()
		state.till_last_report["till-1"] = (at(0), True)

		self.assertEqual(abandoned(state, at(60 * 60), self.DARK), [])

	def test_writing_off_is_not_repeated_on_the_next_sweep(self):
		"""Twice would close a shift that is already closed, or reopen a story."""
		state = self._shop(till_at=at(0), device_at=at(0))
		abandoned(state, at(60 * 60), self.DARK)

		self.assertEqual(abandoned(state, at(61 * 60), self.DARK), [])

	def test_a_branch_that_comes_back_sees_an_ARRIVAL_not_a_silence(self):
		"""The gap has to be honest at both ends.

		Somebody who was in the shop when the till died and is still there when it
		wakes must get a NEW session, not a resumed one — we genuinely could not see
		the room in between and must not claim otherwise.
		"""
		state = self._shop(till_at=at(0), device_at=at(0))
		abandoned(state, at(60 * 60), self.DARK)

		decisions = apply_report(
			state, Report("till-1", at(61 * 60), frozenset({"dev-a"}))
		)

		self.assertEqual([d.kind for d in decisions], [ARRIVED])
		self.assertEqual(decisions[0].at, at(61 * 60))

	def test_an_unsettled_till_still_counts_as_contact(self):
		# Warming up is not silence — we heard from the machine, which is the only
		# question being asked here.
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))
		state.till_last_report["till-1"] = (at(0), False)

		self.assertEqual(abandoned(state, at(10 * 60), self.DARK), [])

	def test_the_freshest_till_decides_how_dark_the_branch_is(self):
		# One till died this morning, another is still talking. Not dark.
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a"})))
		state.till_last_report["till-1"] = (at(0), True)
		state.till_last_report["till-2"] = (at(59 * 60), True)

		self.assertEqual(abandoned(state, at(60 * 60), self.DARK), [])

	def test_last_contact_reads_the_newest_of_several_tills(self):
		state = BranchState()
		state.till_last_report["till-1"] = (at(0), True)
		state.till_last_report["till-2"] = (at(300), True)

		self.assertEqual(last_contact(state), at(300))

	def test_last_contact_of_a_silent_branch_is_None(self):
		self.assertIsNone(last_contact(BranchState()))

	def test_every_device_present_is_written_off_not_just_one(self):
		state = BranchState()
		apply_report(state, Report("till-1", at(0), frozenset({"dev-a", "dev-b", "dev-c"})))
		state.till_last_report["till-1"] = (at(0), True)

		decisions = abandoned(state, at(60 * 60), self.DARK)

		self.assertEqual({d.device_key for d in decisions}, {"dev-a", "dev-b", "dev-c"})
		self.assertEqual(state.present, set())


if __name__ == "__main__":
	unittest.main()
