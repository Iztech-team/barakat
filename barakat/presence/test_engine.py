"""The presence engine, tested as pure functions.

No frappe, no site, no database, no real clock. Every test is a timeline in and a
list of decisions out. This is the whole reason the engine is Frappe-free: the hard
part of this feature is decided here, so it has to be cheap to test exhaustively.
"""

import unittest
from datetime import datetime, timedelta

from barakat.presence.engine import (
	ARRIVED,
	BranchState,
	Report,
	apply_report,
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


if __name__ == "__main__":
	unittest.main()
