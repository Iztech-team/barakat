"""Turning a stream of appeared/gone events into per-device stretches of time.

Frappe-free, like `engine.py` and for the same reason: every hard case in here is
arithmetic on a timeline — an event log that starts mid-story, a device that was never
seen to leave, a pairing that began or ended in the middle of a day — and arithmetic is
worth being able to test without a database.

Why this exists at all: a `Presence Session` records the ONE device that opened it, and
never changes. So a person carrying a phone and a tablet gets a single block coloured
after whichever walked in first, covering hours when that device was not on the network.
The sighting log knows better — it has appeared/gone per device — and this turns that
log back into the intervals the log was made from.

The log is deleted after its retention window, so this can only answer for as long as
the detail survives. Days older than that fall back to the session block, which is why
the caller is told where the detail runs out rather than being handed a silent gap.
"""

APPEARED = "appeared"
GONE = "gone"


class Span:
	"""One device, continuously on the network, from `start` until `end`.

	`open_ended` means it had not left by the end of the window — either it is still
	here, or the log simply stops there. The two are the same to a reader looking at a
	strip, and pretending to know which would be inventing information.
	"""

	__slots__ = ("device_key", "start", "end", "open_ended")

	def __init__(self, device_key, start, end, open_ended=False):
		self.device_key = device_key
		self.start = start
		self.end = end
		self.open_ended = open_ended

	def __eq__(self, other):
		return (
			isinstance(other, Span)
			and self.device_key == other.device_key
			and self.start == other.start
			and self.end == other.end
			and self.open_ended == other.open_ended
		)

	def __repr__(self):
		tail = "…" if self.open_ended else ""
		return f"Span({self.device_key}, {self.start}, {self.end}{tail})"


def _overlap(start, end, window_start, window_end):
	"""The part of [start, end) inside the window, or None if they never touch."""
	low = start if start > window_start else window_start
	high = end if end < window_end else window_end
	return (low, high) if low < high else None


def build_spans(events, window_start, window_end, now, ownership=None):
	"""Fold appeared/gone events into spans, clipped to the window.

	`events` is (device_key, event, at), in any order — it is sorted here, because a
	caller reading from a database sorted by insertion has no guarantee two rows written
	in the same second come back in the order they happened.

	`ownership` maps a device to the stretches during which it belonged to the person we
	are asking about: [(from, to_or_None), …]. A device unpaired at 14:00 must not draw
	past 14:00, and a device paired at noon must not draw the morning it was somebody
	else's — filtering afterwards on "who owns it now" would quietly rewrite history.

	Handled here, all of them seen in real data:

    * a `gone` with no `appeared` — the device was already there when the window opened,
      so the span starts at the window edge rather than being dropped
    * an `appeared` with no `gone` — runs to `now`, marked open-ended
    * repeated `appeared` while already open, or `gone` while already closed — ignored,
      rather than opening a second span or emitting a zero-length one
    * events outside the window entirely — clipped away
    * a span of no length — dropped, because a strip cannot draw it and a tooltip
      claiming "13:04 to 13:04" is noise
	"""
	# Events are grouped by (instant, device) rather than merely sorted, because at a
	# single instant the order in the log carries no meaning and the two possible
	# readings contradict each other. Sorting arrivals first swallows a drop-and-rejoin;
	# sorting departures first turns an arrive-and-leave into "here since midnight, then
	# arrived and never left". Both were reachable, and both were wrong.
	grouped = {}
	for device_key, event, at in events:
		grouped.setdefault((at, device_key), set()).add(event)

	spans = []
	open_at = {}
	closed_one = set()

	for (at, device_key) in sorted(grouped, key=lambda pair: (pair[0], pair[1])):
		events_here = grouped[(at, device_key)]
		arrived = APPEARED in events_here
		departed = GONE in events_here

		if arrived and departed:
			# Both at once. If it was already here this is a drop and an immediate
			# rejoin, which changes nothing; if it was not, it arrived and left in the
			# same instant, which is a stretch of no length. Either way there is
			# nothing to record and nothing to change.
			continue

		if arrived:
			# Already open: the log repeated itself, or a restart re-announced a device
			# it had never reported leaving. Either way the earlier start is the true one.
			if device_key not in open_at:
				open_at[device_key] = at
			continue

		start = open_at.pop(device_key, None)
		if start is None:
			if device_key in closed_one:
				# A second departure for a device that has already left. Reading it as
				# "here since the window opened" would back-date the whole day on the
				# strength of a duplicated row.
				continue
			# Nothing before it: the window opened mid-stay, and it was here from the
			# edge as far as anything we can see goes.
			start = window_start
		closed_one.add(device_key)
		spans.append(Span(device_key, start, at))

	for device_key, start in open_at.items():
		spans.append(Span(device_key, start, now, open_ended=True))

	return _clip(spans, window_start, window_end, ownership)


def _clip(spans, window_start, window_end, ownership):
	"""Cut every span to the window and to the stretches the person owned the device."""
	out = []
	for span in spans:
		inside = _overlap(span.start, span.end, window_start, window_end)
		if not inside:
			continue
		start, end = inside

		windows = (ownership or {}).get(span.device_key)
		if windows is None:
			# No ownership given: the caller is not filtering by who owns what.
			out.append(Span(span.device_key, start, end, span.open_ended and end == span.end))
			continue

		for owned_from, owned_to in windows:
			limit = owned_to if owned_to is not None else end
			piece = _overlap(start, end, owned_from, limit)
			if piece:
				out.append(
					Span(
						span.device_key,
						piece[0],
						piece[1],
						span.open_ended and piece[1] == span.end,
					)
				)

	out.sort(key=lambda s: (s.start, s.device_key))
	return merge_touching(out)


def merge_touching(spans):
	"""Join spans of the SAME device that touch or overlap.

	Note what this does NOT do: it does not close real gaps. A phone that sleeps for
	three minutes and comes back leaves two stretches, and they stay two — that gap is
	the truth about where the phone was.

	It is not needed for flicker, either. A departure is only ever written to the log
	after the wait has expired, so a device that drops and returns inside the wait never
	produces a `gone` at all — the engine has already absorbed it upstream.

	What it is for: stretches that meet exactly. Two events sharing a timestamp produce
	an end and a start on the same instant, and a pairing window that opens where
	another closed does the same. Drawn as two, they put a hairline seam in the strip
	that reads as an absence. Different devices are never merged: that they overlap is
	the whole point of drawing them separately.
	"""
	merged = []
	for span in spans:
		previous = None
		for candidate in merged:
			if candidate.device_key == span.device_key and candidate.end >= span.start:
				previous = candidate
		if previous is None:
			merged.append(Span(span.device_key, span.start, span.end, span.open_ended))
			continue
		if span.end > previous.end:
			previous.end = span.end
			previous.open_ended = span.open_ended
	merged.sort(key=lambda s: (s.start, s.device_key))
	return merged
