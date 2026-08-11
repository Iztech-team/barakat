"""The presence engine, tested as pure functions.

No frappe, no site, no database, no real clock. Every test is a timeline in and a
list of decisions out. This is the whole reason the engine is Frappe-free: the hard
part of this feature is decided here, so it has to be cheap to test exhaustively.
"""

import unittest
from datetime import datetime, timedelta

from barakat.presence.engine import (
	ARRIVED,
	DEPARTED,
	BranchState,
	Report,
	apply_report,
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


if __name__ == "__main__":
	unittest.main()
