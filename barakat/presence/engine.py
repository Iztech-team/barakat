"""Turns raw device sightings into arrivals and departures.

Frappe-free on purpose, exactly like `barakat/persona_matrix.py`: this module holds
every decision the feature makes, so it must be testable with a timeline and nothing
else. If you find yourself wanting `frappe` in here, the thing you want belongs in the
service layer instead.

Two rules from the spec are implemented here and nowhere else:

  - The branch is the unit of truth, not the till. A device is present if ANY till at
    the branch can see it, and gone only when none of them has for the wait period.
  - A departure is never believed immediately. A phone whose screen sleeps drops off
    the wifi while its owner is still at work, so a device that comes back before the
    wait expires produces no event at all.

The engine knows nothing about employees. It decides which *devices* are present; the
service layer maps devices to people. That split is what keeps this module pure and
what makes a device shared between two shifts somebody else's problem.
"""

from dataclasses import dataclass, field

ARRIVED = "arrived"
DEPARTED = "departed"


@dataclass(frozen=True)
class Decision:
	"""Something the branch decided happened. `at` is when it really happened."""

	kind: str
	device_key: str
	at: object


@dataclass(frozen=True)
class Report:
	"""One watcher's view of its branch at one moment.

	`settled` is False while a watcher is still warming up. A freshly started watcher
	knows nothing, and its empty view must never be read as "everybody left".
	"""

	till: str
	at: object
	devices: frozenset
	settled: bool = True


@dataclass
class BranchState:
	"""What one branch currently believes. Mutated in place by `apply_report`."""

	till_last_report: dict = field(default_factory=dict)
	last_seen: dict = field(default_factory=dict)
	present: set = field(default_factory=set)


def apply_report(state, report):
	"""Fold one watcher report into the branch's state. Returns any arrivals."""

	state.till_last_report[report.till] = (report.at, report.settled)

	decisions = []
	for device_key in sorted(report.devices):
		previous = state.last_seen.get(device_key)
		if previous is None or report.at > previous:
			state.last_seen[device_key] = report.at

		if device_key not in state.present:
			state.present.add(device_key)
			decisions.append(Decision(ARRIVED, device_key, report.at))

	return decisions


def tick(state, now, wait, stale_after):
	"""Age out devices nobody has seen for `wait`. Returns any departures.

	`stale_after` guards the case that matters most: if no settled watcher has reported
	recently, the branch is unreachable, not empty. Ageing devices out then would mark
	an entire shop as having gone home because one till lost power.

	A departure's `at` is when the device was last seen, never `now`. Someone who left
	at 17:12 left at 17:12, not at 17:27 when the wait ran out.
	"""

	if not _branch_is_covered(state, now, stale_after):
		return []

	decisions = []
	for device_key in sorted(state.present):
		last_seen = state.last_seen[device_key]
		if now - last_seen > wait:
			state.present.discard(device_key)
			decisions.append(Decision(DEPARTED, device_key, last_seen))

	return decisions


def _branch_is_covered(state, now, stale_after):
	"""True when at least one watcher past its warm-up has reported recently.

	Both halves are required. A watcher still warming up has no opinion worth acting
	on, and a watcher that reported an hour ago is not telling us about now.
	"""

	return any(
		settled and (now - reported_at) <= stale_after
		for reported_at, settled in state.till_last_report.values()
	)
