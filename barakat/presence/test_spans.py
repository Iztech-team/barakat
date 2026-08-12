"""Every way an appeared/gone log can be awkward, as pure timelines.

No frappe, no site, no database, no real clock — the same bargain `test_engine.py`
makes. The point of keeping this logic pure is that the awkward cases are cheap to
write down, so they get written down.
"""

import unittest
from datetime import datetime, timedelta

from barakat.presence.spans import (
	APPEARED,
	GONE,
	Span,
	build_spans,
	merge_touching,
)

DAY = datetime(2026, 8, 11, 0, 0, 0)
NOW = DAY + timedelta(hours=23)
END = DAY + timedelta(days=1)

PHONE = "aaaa1111"
TABLET = "bbbb2222"


def at(hours, minutes=0):
	return DAY + timedelta(hours=hours, minutes=minutes)


def ev(device, event, hours, minutes=0):
	return (device, event, at(hours, minutes))


class TestTheOrdinaryCase(unittest.TestCase):
	def test_one_arrival_and_one_departure_is_one_span(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 8), ev(PHONE, GONE, 17)], DAY, END, NOW
		)
		self.assertEqual(spans, [Span(PHONE, at(8), at(17))])

	def test_two_devices_overlapping_stay_separate(self):
		spans = build_spans(
			[
				ev(PHONE, APPEARED, 8),
				ev(TABLET, APPEARED, 9),
				ev(PHONE, GONE, 11),
				ev(TABLET, GONE, 18),
			],
			DAY,
			END,
			NOW,
		)
		# Overlapping is exactly what we are trying to SHOW; merging them would
		# reproduce the single-block bug this module exists to fix.
		self.assertEqual(
			spans, [Span(PHONE, at(8), at(11)), Span(TABLET, at(9), at(18))]
		)

	def test_events_are_sorted_before_folding(self):
		"""A database sorted by insertion can hand back same-second rows either way."""
		spans = build_spans(
			[ev(PHONE, GONE, 17), ev(PHONE, APPEARED, 8)], DAY, END, NOW
		)
		self.assertEqual(spans, [Span(PHONE, at(8), at(17))])


class TestTruncatedLogs(unittest.TestCase):
	def test_gone_with_no_arrival_starts_at_the_window_edge(self):
		"""The window opened mid-stay. Dropping it would lose a real morning."""
		spans = build_spans([ev(PHONE, GONE, 9)], DAY, END, NOW)
		self.assertEqual(spans, [Span(PHONE, DAY, at(9))])

	def test_arrival_with_no_departure_runs_to_now_and_is_open(self):
		spans = build_spans([ev(PHONE, APPEARED, 8)], DAY, END, NOW)
		self.assertEqual(spans, [Span(PHONE, at(8), NOW, open_ended=True)])

	def test_an_empty_log_is_an_empty_day_not_an_error(self):
		self.assertEqual(build_spans([], DAY, END, NOW), [])


class TestRepeatedEvents(unittest.TestCase):
	def test_a_repeated_arrival_does_not_open_a_second_span(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 8), ev(PHONE, APPEARED, 9), ev(PHONE, GONE, 17)],
			DAY,
			END,
			NOW,
		)
		self.assertEqual(spans, [Span(PHONE, at(8), at(17))])

	def test_a_second_departure_is_ignored_rather_than_extending_the_stay(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 8), ev(PHONE, GONE, 17), ev(PHONE, GONE, 18)],
			DAY,
			END,
			NOW,
		)
		# It left at 17. The duplicate row is not evidence it stayed another hour, and
		# it is not evidence it was here from midnight either — both readings were
		# reachable from the same code path, which is why this case is written down.
		self.assertEqual(spans, [Span(PHONE, at(8), at(17))])


class TestClippingToTheWindow(unittest.TestCase):
	def test_a_span_entirely_before_the_window_is_dropped(self):
		spans = build_spans(
			[
				(PHONE, APPEARED, DAY - timedelta(hours=5)),
				(PHONE, GONE, DAY - timedelta(hours=3)),
			],
			DAY,
			END,
			NOW,
		)
		self.assertEqual(spans, [])

	def test_a_span_straddling_the_start_is_cut_at_the_start(self):
		spans = build_spans(
			[(PHONE, APPEARED, DAY - timedelta(hours=2)), ev(PHONE, GONE, 3)],
			DAY,
			END,
			NOW,
		)
		self.assertEqual(spans, [Span(PHONE, DAY, at(3))])

	def test_a_span_straddling_the_end_is_cut_at_the_end(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 22), (PHONE, GONE, END + timedelta(hours=4))],
			DAY,
			END,
			NOW,
		)
		self.assertEqual(spans, [Span(PHONE, at(22), END)])

	def test_a_zero_length_span_is_dropped(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 9), ev(PHONE, GONE, 9)], DAY, END, NOW
		)
		self.assertEqual(spans, [])


class TestOwnership(unittest.TestCase):
	"""A device only draws while it actually belonged to the person being asked about."""

	def test_a_device_paired_at_noon_does_not_draw_the_morning(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 8), ev(PHONE, GONE, 17)],
			DAY,
			END,
			NOW,
			ownership={PHONE: [(at(12), None)]},
		)
		self.assertEqual(spans, [Span(PHONE, at(12), at(17))])

	def test_a_device_unpaired_at_two_does_not_draw_the_afternoon(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 8), ev(PHONE, GONE, 17)],
			DAY,
			END,
			NOW,
			ownership={PHONE: [(DAY, at(14))]},
		)
		self.assertEqual(spans, [Span(PHONE, at(8), at(14))])

	def test_a_device_that_was_never_theirs_draws_nothing(self):
		spans = build_spans(
			[ev(PHONE, APPEARED, 8), ev(PHONE, GONE, 17)],
			DAY,
			END,
			NOW,
			ownership={PHONE: []},
		)
		self.assertEqual(spans, [])

	def test_a_device_paired_twice_draws_both_stretches(self):
		"""Handed back and then handed out again — two stretches, one gap."""
		spans = build_spans(
			[ev(PHONE, APPEARED, 6), ev(PHONE, GONE, 20)],
			DAY,
			END,
			NOW,
			ownership={PHONE: [(at(7), at(10)), (at(15), None)]},
		)
		self.assertEqual(
			spans, [Span(PHONE, at(7), at(10)), Span(PHONE, at(15), at(20))]
		)

	def test_an_open_span_cut_short_by_unpairing_is_no_longer_open(self):
		"""It did not run to now — it stopped being theirs. Saying "still here" would lie."""
		spans = build_spans(
			[ev(PHONE, APPEARED, 8)],
			DAY,
			END,
			NOW,
			ownership={PHONE: [(DAY, at(14))]},
		)
		self.assertEqual(spans, [Span(PHONE, at(8), at(14), open_ended=False)])

	def test_ownership_of_one_device_does_not_affect_another(self):
		spans = build_spans(
			[
				ev(PHONE, APPEARED, 8),
				ev(PHONE, GONE, 17),
				ev(TABLET, APPEARED, 8),
				ev(TABLET, GONE, 17),
			],
			DAY,
			END,
			NOW,
			ownership={PHONE: [(at(12), None)], TABLET: [(DAY, None)]},
		)
		self.assertEqual(
			spans, [Span(TABLET, at(8), at(17)), Span(PHONE, at(12), at(17))]
		)


class TestFlicker(unittest.TestCase):
	def test_a_real_gap_of_seconds_stays_two_spans(self):
		"""A gap is the truth about where the phone was, however small.

		This cannot arise from flicker: a departure is only written after the wait has
		expired, so a device that drops and returns inside the wait never produces a
		`gone` at all. If two stretches DO reach here with a gap between them, the gap
		is real and closing it would be inventing presence.
		"""
		spans = build_spans(
			[
				ev(PHONE, APPEARED, 8),
				(PHONE, GONE, at(11) + timedelta(seconds=2)),
				(PHONE, APPEARED, at(11) + timedelta(seconds=4)),
				ev(PHONE, GONE, 17),
			],
			DAY,
			END,
			NOW,
		)
		self.assertEqual(
			spans,
			[
				Span(PHONE, at(8), at(11) + timedelta(seconds=2)),
				Span(PHONE, at(11) + timedelta(seconds=4), at(17)),
			],
		)

	def test_a_drop_and_rejoin_in_the_same_second_is_still_one_span(self):
		"""Both events carry the same stamp, so their order in the log means nothing."""
		spans = build_spans(
			[
				ev(PHONE, APPEARED, 8),
				ev(PHONE, APPEARED, 11),
				ev(PHONE, GONE, 11),
				ev(PHONE, GONE, 17),
			],
			DAY,
			END,
			NOW,
		)
		self.assertEqual(spans, [Span(PHONE, at(8), at(17))])

	def test_a_real_gap_is_kept_as_two_spans(self):
		spans = build_spans(
			[
				ev(PHONE, APPEARED, 8),
				ev(PHONE, GONE, 12),
				ev(PHONE, APPEARED, 13),
				ev(PHONE, GONE, 18),
			],
			DAY,
			END,
			NOW,
		)
		self.assertEqual(
			spans, [Span(PHONE, at(8), at(12)), Span(PHONE, at(13), at(18))]
		)

	def test_merging_never_joins_two_different_devices(self):
		merged = merge_touching(
			[Span(PHONE, at(8), at(12)), Span(TABLET, at(12), at(18))]
		)
		self.assertEqual(len(merged), 2)

	def test_merging_keeps_the_later_end_and_its_openness(self):
		merged = merge_touching(
			[Span(PHONE, at(8), at(12)), Span(PHONE, at(11), NOW, open_ended=True)]
		)
		self.assertEqual(merged, [Span(PHONE, at(8), NOW, open_ended=True)])


class TestOvernight(unittest.TestCase):
	def test_a_stretch_across_midnight_is_one_span_not_two(self):
		"""Splitting by day is a DRAWING decision, made on the screen, not here."""
		spans = build_spans(
			[
				(PHONE, APPEARED, DAY + timedelta(hours=22)),
				(PHONE, GONE, DAY + timedelta(days=1, hours=6)),
			],
			DAY,
			DAY + timedelta(days=2),
			NOW,
		)
		self.assertEqual(
			spans,
			[Span(PHONE, at(22), DAY + timedelta(days=1, hours=6))],
		)


if __name__ == "__main__":
	unittest.main()
