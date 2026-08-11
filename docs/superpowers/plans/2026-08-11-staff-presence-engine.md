# Staff presence — engine and data model implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the presence decision engine and its data model inside the `barakat` app, with the tenant boundary and persona permissions enforced, so a later plan can feed it real sightings.

**Architecture:** A Frappe-free pure module (`barakat/presence/engine.py`) holds every decision — who is present, when they arrived, when they left. Frappe doctypes hold the data. The two are joined by a thin service layer in a later plan. Purity is the point: the hard logic is tested with a timeline and no site, no clock and no database.

**Tech Stack:** Python 3.11, Frappe v15, `FrappeTestCase` for site tests, plain `unittest` for the pure engine.

## Scope

This is **plan 1 of 5**. It covers spec steps B and C only.

| Plan | Covers | Repo |
|---|---|---|
| **1 (this one)** | pure engine, doctypes, company scoping, permissions | `barakat` |
| 2 | till enrollment, keys, the report endpoint, the service layer | `barakat` |
| 3 | the watcher (Windows background service) | `barakat-electrobun` |
| 4 | settings / tills / pairing screens | `admin_panel_barakat` + `proxy-barakat` |
| 5 | shadow mode, no-data list, alarms, check-in creation | `barakat` + `admin_panel_barakat` |

At the end of this plan nothing is user-visible. That is expected. What exists is a provably correct engine and a data model that cannot leak across tenants.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-staff-presence-design.md`. Read §8 (the engine), §7 (data model) and §10.7–10.11 (permissions) before starting.
- **`barakat/presence/engine.py` must never import `frappe`.** It is imported by pure unittests. Same rule and same reason as `barakat/persona_matrix.py`.
- **Every presence doctype carries a `custom_company` field, `Link → Company`, `reqd: 1`.** Mandatory, because a blank company marker is visible to everyone (spec §10.8). `custom_company` not `company`, matching `POS Scale Settings`.
- **No doctype may be added to `COMPANY_NEUTRAL_DOCTYPES`.** Every one of these is shop-owned.
- Module name in every doctype JSON is `Barakat`.
- Doctype files live at `barakat/barakat/doctype/<snake_name>/<snake_name>.json`.
- Work on branch `dev`, in `Desktop\barakat-qa\barakat-dev`. Never check out `test` or `main` there.
- Commit after every task. Push to `dev` at the end of the plan, not before.

---

### Task 1: The pure engine — module and arrivals

**Files:**
- Create: `barakat/presence/__init__.py`
- Create: `barakat/presence/engine.py`
- Test: `barakat/presence/test_engine.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Decision(kind, device_key, at)`, `Report(till, at, devices, settled)`, `BranchState()`, `apply_report(state, report) -> list[Decision]`, and the constants `ARRIVED = "arrived"` and `DEPARTED = "departed"`. Later tasks and plans import all of these from `barakat.presence.engine`.

- [ ] **Step 1: Write the failing test**

Create `barakat/presence/test_engine.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && python -m unittest barakat.presence.test_engine -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'barakat.presence'`.

- [ ] **Step 3: Write minimal implementation**

Create `barakat/presence/__init__.py` as an empty file.

Create `barakat/presence/engine.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && python -m unittest barakat.presence.test_engine -v
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add barakat/presence/ && git commit -m "feat(presence): pure engine, arrivals"
```

---

### Task 2: The pure engine — departures and the wait

**Files:**
- Modify: `barakat/presence/engine.py`
- Test: `barakat/presence/test_engine.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces: `tick(state, now, wait, stale_after) -> list[Decision]`, where `wait` and `stale_after` are `timedelta`. Returns `DEPARTED` decisions whose `at` is when the device was last seen, **not** `now`.

- [ ] **Step 1: Write the failing test**

Append to `barakat/presence/test_engine.py`, above the `if __name__` block:

```python
class TestDepartures(unittest.TestCase):
	WAIT = timedelta(minutes=15)
	STALE = timedelta(minutes=5)

	def _present(self, *devices, till="till-1", when=None):
		state = BranchState()
		apply_report(
			state, Report(till, when or at(0), frozenset(devices))
		)
		return state

	def test_no_departure_before_the_wait_expires(self):
		state = self._present("dev-a")

		decisions = tick(state, at(14 * 60), self.WAIT, self.STALE)

		self.assertEqual(decisions, [])

	def test_departure_once_the_wait_expires(self):
		state = self._present("dev-a")

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, DEPARTED)
		self.assertEqual(decisions[0].device_key, "dev-a")

	def test_departure_time_is_when_the_device_vanished_not_when_the_timer_finished(self):
		"""Spec 8.3: out_time is the real disappearance, not the timer expiry."""
		state = self._present("dev-a")

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(decisions[0].at, at(0))

	def test_a_device_that_comes_back_in_time_produces_no_event_at_all(self):
		"""The pocket-sleep case. Not a departure and not a second arrival."""
		state = self._present("dev-a")

		self.assertEqual(tick(state, at(10 * 60), self.WAIT, self.STALE), [])
		back = apply_report(state, Report("till-1", at(11 * 60), frozenset({"dev-a"})))
		later = tick(state, at(20 * 60), self.WAIT, self.STALE)

		self.assertEqual(back, [])
		self.assertEqual(later, [])

	def test_flapping_twenty_times_still_produces_no_event(self):
		state = self._present("dev-a")
		for minute in range(1, 41, 2):
			tick(state, at(minute * 60), self.WAIT, self.STALE)
			apply_report(
				state, Report("till-1", at((minute + 1) * 60), frozenset({"dev-a"}))
			)

		self.assertIn("dev-a", state.present)

	def test_departing_then_returning_is_a_fresh_arrival(self):
		state = self._present("dev-a")
		tick(state, at(16 * 60), self.WAIT, self.STALE)

		decisions = apply_report(
			state, Report("till-1", at(17 * 60), frozenset({"dev-a"}))
		)

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].kind, ARRIVED)

	def test_one_till_still_seeing_the_device_keeps_it_present(self):
		"""Present if ANY till sees it. Not a vote."""
		state = self._present("dev-a")
		apply_report(state, Report("till-2", at(14 * 60), frozenset({"dev-a"})))

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)

		self.assertEqual(decisions, [])

	def test_departure_only_once_every_till_has_lost_it(self):
		state = self._present("dev-a")
		apply_report(state, Report("till-2", at(60), frozenset({"dev-a"})))
		apply_report(state, Report("till-1", at(120), frozenset()))
		apply_report(state, Report("till-2", at(120), frozenset()))

		decisions = tick(state, at(17 * 60), self.WAIT, self.STALE)

		self.assertEqual(len(decisions), 1)
		self.assertEqual(decisions[0].at, at(60))
```

Update the import at the top of the file to add `DEPARTED` and `tick`:

```python
from barakat.presence.engine import (
	ARRIVED,
	DEPARTED,
	BranchState,
	Report,
	apply_report,
	tick,
)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && python -m unittest barakat.presence.test_engine -v
```

Expected: FAIL with `ImportError: cannot import name 'tick'`.

- [ ] **Step 3: Write minimal implementation**

Append to `barakat/presence/engine.py`:

```python
def tick(state, now, wait, stale_after):
	"""Age out devices nobody has seen for `wait`. Returns any departures.

	`stale_after` guards the case that matters most: if no settled watcher has reported
	recently, the branch is unreachable, not empty. Ageing devices out then would mark
	an entire shop as having gone home because one till lost power.
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
	"""True when at least one watcher past its warm-up has reported recently."""

	return any(
		settled and (now - reported_at) <= stale_after
		for reported_at, settled in state.till_last_report.values()
	)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && python -m unittest barakat.presence.test_engine -v
```

Expected: PASS, 13 tests.

Note the tests keep the branch covered by reporting inside `STALE` of each `tick`, except where a test is deliberately about staleness. If `test_departure_once_the_wait_expires` fails with an empty list, that is `_branch_is_covered` doing its job — the test's last report is at `at(0)` and `now` is 16 minutes later. Fix the test by adding an empty keep-alive report, not by weakening the guard:

```python
	def test_departure_once_the_wait_expires(self):
		state = self._present("dev-a")
		apply_report(state, Report("till-1", at(16 * 60), frozenset()))

		decisions = tick(state, at(16 * 60), self.WAIT, self.STALE)
```

Apply the same keep-alive line to `test_departure_time_is_when_the_device_vanished_not_when_the_timer_finished` and `test_departing_then_returning_is_a_fresh_arrival`.

- [ ] **Step 5: Commit**

```bash
git add barakat/presence/ && git commit -m "feat(presence): departures with the wait, merged across tills"
```

---

### Task 3: The pure engine — warm-up and unreachable branches

**Files:**
- Test: `barakat/presence/test_engine.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 2. No new production code — this task proves `_branch_is_covered` handles both failure shapes, and exists because these are the two cases that silently corrupt a whole day of attendance.

- [ ] **Step 1: Write the failing test**

Append to `barakat/presence/test_engine.py`:

```python
class TestCoverage(unittest.TestCase):
	"""The two ways a branch stops being trustworthy, and neither may look like 'empty'."""

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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && python -m unittest barakat.presence.test_engine.TestCoverage -v
```

Expected: at least `test_a_warming_up_watcher_cannot_cause_departures` FAILS if `settled` is being ignored. If all five pass immediately, the implementation from Task 2 was already correct — record that and move on rather than inventing a change.

- [ ] **Step 3: Write minimal implementation**

No production change is expected. If a test fails, the fix belongs in `_branch_is_covered` in `barakat/presence/engine.py` — it must require **both** `settled` and recency, never either alone.

- [ ] **Step 4: Run the whole engine suite**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && python -m unittest barakat.presence.test_engine -v
```

Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add barakat/presence/ && git commit -m "test(presence): warm-up and unreachable branches never look empty"
```

---

### Task 4: `Presence Settings` and the per-company mode gate

**Files:**
- Create: `barakat/barakat/doctype/presence_settings/presence_settings.json`
- Create: `barakat/barakat/doctype/presence_settings/presence_settings.py`
- Create: `barakat/barakat/doctype/presence_settings/__init__.py`
- Create: `barakat/presence/mode.py`
- Test: `barakat/presence/test_mode.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `barakat.presence.mode.is_wifi_mode(company) -> bool` and `barakat.presence.mode.settings_for(company) -> dict`. Plan 2's endpoint refuses every request where `is_wifi_mode` is False.

- [ ] **Step 1: Write the failing test**

Create `barakat/presence/test_mode.py`:

```python
"""Wifi presence is off unless a company deliberately turns it on.

This is the switch that keeps `petromall` and every untouched company completely
unaffected: no endpoint, no jobs, no screens. The default matters more than the
feature, so it is tested first.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.presence.mode import is_wifi_mode, settings_for


class TestPresenceMode(FrappeTestCase):
	def setUp(self):
		self.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		frappe.db.delete("Presence Settings", {"custom_company": self.company})

	def test_a_company_with_no_settings_row_is_off(self):
		self.assertFalse(is_wifi_mode(self.company))

	def test_a_company_with_manual_mode_is_off(self):
		self._make(mode="Manual")
		self.assertFalse(is_wifi_mode(self.company))

	def test_a_company_with_wifi_mode_is_on(self):
		self._make(mode="Wifi")
		self.assertTrue(is_wifi_mode(self.company))

	def test_defaults_are_returned_when_no_row_exists(self):
		values = settings_for(self.company)

		self.assertEqual(values["departure_wait_minutes"], 15)
		self.assertEqual(values["sweep_interval_s"], 2)
		self.assertEqual(values["warmup_s"], 60)
		self.assertEqual(values["sighting_retention_days"], 30)
		self.assertEqual(values["max_devices"], 512)

	def test_a_saved_value_overrides_the_default(self):
		self._make(mode="Wifi", departure_wait_minutes=8)

		self.assertEqual(settings_for(self.company)["departure_wait_minutes"], 8)

	def test_company_is_mandatory(self):
		doc = frappe.new_doc("Presence Settings")
		doc.mode = "Wifi"

		with self.assertRaises(frappe.MandatoryError):
			doc.insert()

	def _make(self, **values):
		doc = frappe.new_doc("Presence Settings")
		doc.custom_company = self.company
		doc.update(values)
		doc.insert()
		return doc
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> run-tests --module barakat.presence.test_mode
```

Run this inside the local Docker bench (see the `barakat-local` skill). Expected: FAIL with `DoesNotExistError: DocType Presence Settings not found`.

- [ ] **Step 3: Write minimal implementation**

Create `barakat/barakat/doctype/presence_settings/__init__.py` as an empty file.

Create `barakat/barakat/doctype/presence_settings/presence_settings.json`:

```json
{
 "actions": [],
 "autoname": "field:custom_company",
 "creation": "2026-08-11 00:00:00",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": ["custom_company","mode","departure_wait_minutes","sweep_interval_s","heartbeat_s","warmup_s","sighting_retention_days","pairing_timeout_s","rot_days","max_devices"],
 "fields": [
  {"fieldname":"custom_company","fieldtype":"Link","label":"Company","options":"Company","reqd":1,"unique":1},
  {"fieldname":"mode","fieldtype":"Select","label":"Attendance Mode","options":"Manual\nWifi","default":"Manual","reqd":1},
  {"fieldname":"departure_wait_minutes","fieldtype":"Int","label":"Minutes before a missing phone counts as gone","default":"15"},
  {"fieldname":"sweep_interval_s","fieldtype":"Int","label":"Seconds between scans","default":"2"},
  {"fieldname":"heartbeat_s","fieldtype":"Int","label":"Seconds between heartbeats","default":"30"},
  {"fieldname":"warmup_s","fieldtype":"Int","label":"Warm-up seconds after a till starts","default":"60"},
  {"fieldname":"sighting_retention_days","fieldtype":"Int","label":"Days of raw sightings kept","default":"30"},
  {"fieldname":"pairing_timeout_s","fieldtype":"Int","label":"Pairing session timeout (seconds)","default":"120"},
  {"fieldname":"rot_days","fieldtype":"Int","label":"Days before a pairing is flagged as rotted","default":"7"},
  {"fieldname":"max_devices","fieldtype":"Int","label":"Maximum devices per report","default":"512"}
 ],
 "links": [],
 "modified": "2026-08-11 00:00:00",
 "module": "Barakat",
 "name": "Presence Settings",
 "owner": "Administrator",
 "permissions": [
  {"role":"System Manager","read":1,"write":1,"create":1,"delete":1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC"
}
```

Create `barakat/barakat/doctype/presence_settings/presence_settings.py`:

```python
import frappe
from frappe.model.document import Document


class PresenceSettings(Document):
	pass
```

Create `barakat/presence/mode.py`:

```python
"""Is wifi presence switched on for this company, and with what numbers.

Every presence entry point starts here. A company that has never been switched on has
no settings row at all, and must behave exactly as it did before this feature existed:
no endpoint, no jobs, no screens. `petromall` is that case permanently, and it is why
the default lives in code rather than in a row someone has to remember to create.
"""

import frappe

DEFAULTS = {
	"mode": "Manual",
	"departure_wait_minutes": 15,
	"sweep_interval_s": 2,
	"heartbeat_s": 30,
	"warmup_s": 60,
	"sighting_retention_days": 30,
	"pairing_timeout_s": 120,
	"rot_days": 7,
	"max_devices": 512,
}


def settings_for(company):
	"""The company's presence settings, falling back to `DEFAULTS` field by field."""

	values = dict(DEFAULTS)
	if not company:
		return values

	row = frappe.db.get_value(
		"Presence Settings",
		{"custom_company": company},
		list(DEFAULTS),
		as_dict=True,
	)
	if not row:
		return values

	for key in DEFAULTS:
		if row.get(key) not in (None, ""):
			values[key] = row[key]
	return values


def is_wifi_mode(company):
	"""True only when this company has deliberately turned wifi presence on."""

	return settings_for(company)["mode"] == "Wifi"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> migrate && sudo -u frappe bench --site <local-site> run-tests --module barakat.presence.test_mode
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add barakat/barakat/doctype/presence_settings/ barakat/presence/ && git commit -m "feat(presence): per-company mode switch, off by default"
```

---

### Task 5: The presence doctypes

**Files:**
- Create: `barakat/barakat/doctype/presence_till/presence_till.json` + `.py` + `__init__.py`
- Create: `barakat/barakat/doctype/presence_device/presence_device.json` + `.py` + `__init__.py`
- Create: `barakat/barakat/doctype/employee_device/employee_device.json` + `.py` + `__init__.py`
- Create: `barakat/barakat/doctype/presence_sighting/presence_sighting.json` + `.py` + `__init__.py`
- Create: `barakat/barakat/doctype/presence_session/presence_session.json` + `.py` + `__init__.py`
- Test: `barakat/presence/test_doctypes.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the five doctypes named above. Plan 2 writes `Presence Sighting` by direct SQL and reads `Presence Till` by `api_user`.

`Presence Pairing Session` and `Presence Alert` are deliberately **not** in this plan — they belong with the features that use them (plans 4 and 5). Creating them now would mean five unused tables and a company-scope decision nobody can justify yet.

- [ ] **Step 1: Write the failing test**

Create `barakat/presence/test_doctypes.py`:

```python
"""Every presence doctype is shop-owned, and none may exist without a company.

The 2026-08-05 Contact / Item Price leak happened because a doctype had nothing for a
Company User Permission to bind to, and because a BLANK marker is visible to everyone.
Both halves are prevented here at the schema level: `custom_company` is a Link, and it
is mandatory, so the blank case cannot be reached at all.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

PRESENCE_DOCTYPES = (
	"Presence Settings",
	"Presence Till",
	"Presence Device",
	"Employee Device",
	"Presence Sighting",
	"Presence Session",
)


class TestPresenceDoctypes(FrappeTestCase):
	def test_every_presence_doctype_exists(self):
		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("DocType", doctype))

	def test_every_presence_doctype_has_a_company_link(self):
		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				meta = frappe.get_meta(doctype)
				field = meta.get_field("custom_company")
				self.assertIsNotNone(field, f"{doctype} has no custom_company field")
				self.assertEqual(field.fieldtype, "Link")
				self.assertEqual(field.options, "Company")

	def test_company_is_mandatory_on_every_presence_doctype(self):
		"""A blank marker is visible to everyone. Mandatory removes the case."""
		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				meta = frappe.get_meta(doctype)
				self.assertTrue(
					meta.get_field("custom_company").reqd,
					f"{doctype}.custom_company must be reqd",
				)

	def test_a_device_may_belong_to_only_one_employee_at_a_time(self):
		company = frappe.get_all("Company", pluck="name", limit=1)[0]
		employees = frappe.get_all("Employee", pluck="name", limit=2)
		if len(employees) < 2:
			self.skipTest("needs two Employee records")

		frappe.db.delete("Employee Device", {"device_key": "test-key-1"})
		first = frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": company,
				"employee": employees[0],
				"device_key": "test-key-1",
				"valid_from": "2026-01-01",
			}
		).insert()
		self.assertTrue(first.name)

		second = frappe.get_doc(
			{
				"doctype": "Employee Device",
				"custom_company": company,
				"employee": employees[1],
				"device_key": "test-key-1",
				"valid_from": "2026-01-01",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			second.insert()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> run-tests --module barakat.presence.test_doctypes
```

Expected: FAIL — `Presence Till` and the rest do not exist.

- [ ] **Step 3: Write minimal implementation**

Each doctype needs an empty `__init__.py`, a `.json`, and a `.py` containing a `Document` subclass. Use the `Presence Settings` files from Task 4 as the shape reference.

`presence_till.json` fields:

```json
 "field_order": ["custom_company","pos_profile","branch","machine_name","machine_fingerprint","api_user","status","last_seen","last_seq","watcher_version","last_clock_drift_s","approved_by","approved_at"],
 "fields": [
  {"fieldname":"custom_company","fieldtype":"Link","label":"Company","options":"Company","reqd":1},
  {"fieldname":"pos_profile","fieldtype":"Link","label":"POS Profile","options":"POS Profile","reqd":1,"unique":1},
  {"fieldname":"branch","fieldtype":"Link","label":"Branch","options":"Branch","read_only":1},
  {"fieldname":"machine_name","fieldtype":"Data","label":"Computer"},
  {"fieldname":"machine_fingerprint","fieldtype":"Data","label":"Machine Fingerprint"},
  {"fieldname":"api_user","fieldtype":"Link","label":"API User","options":"User","read_only":1},
  {"fieldname":"status","fieldtype":"Select","label":"Status","options":"Pending\nActive\nSuspended\nRetired","default":"Pending","reqd":1},
  {"fieldname":"last_seen","fieldtype":"Datetime","label":"Last Seen","read_only":1},
  {"fieldname":"last_seq","fieldtype":"Int","label":"Last Sequence","read_only":1},
  {"fieldname":"watcher_version","fieldtype":"Data","label":"Watcher Version","read_only":1},
  {"fieldname":"last_clock_drift_s","fieldtype":"Int","label":"Clock Drift (s)","read_only":1},
  {"fieldname":"approved_by","fieldtype":"Link","label":"Approved By","options":"User","read_only":1},
  {"fieldname":"approved_at","fieldtype":"Datetime","label":"Approved At","read_only":1}
 ],
```

`presence_till.py` — the branch is read from the POS Profile, never accepted from a caller. A mislabelled till puts one person in two branches at once (spec §7.2):

```python
import frappe
from frappe.model.document import Document


class PresenceTill(Document):
	def validate(self):
		"""Branch and company come from the POS Profile, never from the caller."""

		profile = frappe.db.get_value(
			"POS Profile", self.pos_profile, ["company", "branch"], as_dict=True
		)
		if not profile:
			frappe.throw(f"POS Profile {self.pos_profile} not found")

		self.custom_company = profile.company
		self.branch = profile.branch
```

`presence_device.json` fields:

```json
 "field_order": ["custom_company","device_key","raw_id","display_suffix","is_randomised","first_seen","last_seen","days_seen"],
 "fields": [
  {"fieldname":"custom_company","fieldtype":"Link","label":"Company","options":"Company","reqd":1},
  {"fieldname":"device_key","fieldtype":"Data","label":"Device Key","reqd":1},
  {"fieldname":"raw_id","fieldtype":"Data","label":"Raw Id"},
  {"fieldname":"display_suffix","fieldtype":"Data","label":"Shown As","length":8},
  {"fieldname":"is_randomised","fieldtype":"Check","label":"Privacy Address","default":"0"},
  {"fieldname":"first_seen","fieldtype":"Datetime","label":"First Seen"},
  {"fieldname":"last_seen","fieldtype":"Datetime","label":"Last Seen"},
  {"fieldname":"days_seen","fieldtype":"Int","label":"Days Seen","default":"0"}
 ],
```

`employee_device.json` fields:

```json
 "field_order": ["custom_company","employee","device_key","valid_from","valid_to","paired_by","notes"],
 "fields": [
  {"fieldname":"custom_company","fieldtype":"Link","label":"Company","options":"Company","reqd":1},
  {"fieldname":"employee","fieldtype":"Link","label":"Employee","options":"Employee","reqd":1},
  {"fieldname":"device_key","fieldtype":"Data","label":"Device Key","reqd":1},
  {"fieldname":"valid_from","fieldtype":"Date","label":"Valid From","reqd":1},
  {"fieldname":"valid_to","fieldtype":"Date","label":"Valid To"},
  {"fieldname":"paired_by","fieldtype":"Link","label":"Paired By","options":"User","read_only":1},
  {"fieldname":"notes","fieldtype":"Small Text","label":"Notes"}
 ],
```

`employee_device.py` — one owner at a time, and pairings are closed, never deleted (spec §7.4):

```python
import frappe
from frappe.model.document import Document


class EmployeeDevice(Document):
	def validate(self):
		"""A device may belong to only one employee at a time.

		Open rows are those with no `valid_to`. Closing a pairing rather than deleting
		it is what keeps last January's attendance explicable, so this check looks only
		at open rows and never at history.
		"""

		if self.valid_to:
			return

		clash = frappe.db.exists(
			"Employee Device",
			{
				"device_key": self.device_key,
				"custom_company": self.custom_company,
				"valid_to": ("is", "not set"),
				"name": ("!=", self.name or ""),
			},
		)
		if clash:
			owner = frappe.db.get_value("Employee Device", clash, "employee")
			frappe.throw(
				f"This device is already paired to {owner}. "
				"Close that pairing before opening a new one."
			)
```

`presence_sighting.json` fields — written by direct SQL in plan 2, so keep it flat:

```json
 "field_order": ["custom_company","branch","till","device_key","event","server_time"],
 "fields": [
  {"fieldname":"custom_company","fieldtype":"Link","label":"Company","options":"Company","reqd":1},
  {"fieldname":"branch","fieldtype":"Link","label":"Branch","options":"Branch","reqd":1},
  {"fieldname":"till","fieldtype":"Link","label":"Till","options":"Presence Till"},
  {"fieldname":"device_key","fieldtype":"Data","label":"Device Key","reqd":1},
  {"fieldname":"event","fieldtype":"Select","label":"Event","options":"appeared\ngone","reqd":1},
  {"fieldname":"server_time","fieldtype":"Datetime","label":"Server Time","reqd":1}
 ],
```

`presence_session.json` fields:

```json
 "field_order": ["custom_company","branch","employee","in_time","out_time","state","checkin_in","checkin_out"],
 "fields": [
  {"fieldname":"custom_company","fieldtype":"Link","label":"Company","options":"Company","reqd":1},
  {"fieldname":"branch","fieldtype":"Link","label":"Branch","options":"Branch","reqd":1},
  {"fieldname":"employee","fieldtype":"Link","label":"Employee","options":"Employee","reqd":1},
  {"fieldname":"in_time","fieldtype":"Datetime","label":"In","reqd":1},
  {"fieldname":"out_time","fieldtype":"Datetime","label":"Out"},
  {"fieldname":"state","fieldtype":"Select","label":"State","options":"Open\nClosed\nSuperseded","default":"Open","reqd":1},
  {"fieldname":"checkin_in","fieldtype":"Link","label":"Check-in (IN)","options":"Employee Checkin","read_only":1},
  {"fieldname":"checkin_out","fieldtype":"Link","label":"Check-in (OUT)","options":"Employee Checkin","read_only":1}
 ],
```

`presence_device.py`, `presence_sighting.py` and `presence_session.py` each contain only a `Document` subclass with `pass`, matching `presence_settings.py`.

Every JSON uses the same `permissions` block as `Presence Settings` — `System Manager` only for now. Persona access is added in Task 7.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> migrate && sudo -u frappe bench --site <local-site> run-tests --module barakat.presence.test_doctypes
```

Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add barakat/barakat/doctype/ barakat/presence/ && git commit -m "feat(presence): tills, devices, pairings, sightings and sessions"
```

---

### Task 6: Register the doctypes with the tenant boundary

**Files:**
- Modify: `barakat/persona_matrix.py` — the `MODULE_DOCTYPES` map
- Test: `barakat/overrides/test_company_scope.py` (existing, must pass unchanged)
- Test: `barakat/test_persona_matches_matrix.py` (existing, must pass unchanged)

**Interfaces:**
- Consumes: the doctypes from Tasks 4 and 5.
- Produces: `GUARDED_DOCTYPES` now includes every presence doctype, so `company_scope`'s query conditions and `has_permission` hooks apply to them automatically.

**Read first:** `barakat/overrides/company_scope.py`'s module docstring. `GUARDED_DOCTYPES` is derived from `MODULE_DOCTYPES`, and `test_company_scope.py` fails until each new doctype either has a company marker or is declared site-wide. Expect it to fail. That failure is the test doing its job.

- [ ] **Step 1: Run the existing scope test to see it pass before the change**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> run-tests --module barakat.overrides.test_company_scope
```

Expected: PASS. This is the baseline — if it is already failing, stop and fix that first; it is unrelated to this work.

- [ ] **Step 2: Add the doctypes to the module map**

In `barakat/persona_matrix.py`, change the `attendance`, `staff` and `settings` entries of `MODULE_DOCTYPES`.

Find:

```python
	"attendance": ("Attendance",),
```

Replace with:

```python
	# Presence rows are read by whoever reads attendance: Manager and HR. They are the
	# evidence behind an automatic check-in, so someone querying an attendance record
	# has to be able to see where it came from.
	"attendance": (
		"Attendance",
		"Presence Device",
		"Presence Session",
		"Presence Sighting",
	),
```

Then add `Employee Device` to the `staff` entry and `Presence Settings` + `Presence Till` to the `settings` entry, keeping each tuple's existing members. Pairing lives under `staff` on purpose: `staff: write` is Manager-only, and pairing a device is the one action in this feature that can be used to commit fraud. HR is `staff: read`, so HR can see pairings and cannot create them.

- [ ] **Step 3: Run the scope test**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> run-tests --module barakat.overrides.test_company_scope
```

**Expected: PASS, with no further change.** Verified by reading `company_field_for` in `barakat/overrides/company_scope.py` on 2026-08-11: it resolves the marker by *name*, trying `company` then `custom_company`, and accepts either as long as it is a `Link` whose `options` is `Company`. Our doctypes are named `custom_company` and are real fields, so they resolve with no entry in `COMPANY_FIELD_OVERRIDES`.

`COMPANY_FIELD_OVERRIDES` exists only for doctypes the two standard names miss — `Branch` (`custom_pos_company`) and `Company` (`name`). **Do not add presence doctypes to it**, and do not add anything to `COMPANY_NEUTRAL_DOCTYPES`; every presence doctype is shop-owned.

If it does FAIL, the message names which doctype could not be pinned. The fix is on that doctype's marker field — check it is `Link`, `options: Company`, and named exactly `custom_company` — not a new override entry.

- [ ] **Step 4: Run both guard tests together**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> run-tests --module barakat.overrides.test_company_scope && sudo -u frappe bench --site <local-site> run-tests --module barakat.test_persona_matches_matrix
```

Expected: both PASS.

`test_persona_matches_matrix` compares `persona_matrix.py` against `persona_matrix.json`, which is the byte-identical snapshot shared with the proxy. `MODULE_DOCTYPES` is not in that snapshot — only the persona bundles are — so this change must not require touching the JSON. **If that test fails, stop.** It means the snapshot does carry doctypes, and the proxy repo needs the identical change in the same release. Do not edit the JSON to make the test green.

- [ ] **Step 5: Commit**

```bash
git add barakat/persona_matrix.py barakat/overrides/company_scope.py && git commit -m "feat(presence): bring presence doctypes inside the company boundary"
```

---

### Task 7: Permissions, including the owner blind spot

**Files:**
- Modify: `barakat/setup/install.py`
- Test: `barakat/presence/test_permissions.py`

**Interfaces:**
- Consumes: the doctypes from Tasks 4–5 and the module registration from Task 6.
- Produces: `barakat.setup.install.grant_owner_presence_perms()`, called from both `after_install` and `after_migrate`.

**Read first:** spec §10.10. Two traps apply here.

- [ ] **Step 1: Write the failing test**

Create `barakat/presence/test_permissions.py`:

```python
"""Who can see and change presence data.

Two failures this guards, both of which have happened before in this codebase:

  - The OWNER blind spot. Owner accounts hold System Manager and no persona, and hit
    ERPNext under their own native roles. A doctype permed only through a Barakat
    persona role renders in the admin panel and 403s for the owner. Here that would
    mean a shop owner cannot see his own staff's attendance.
  - The tenant boundary. Presence rows are shop-owned; a manager of one company must
    not see another company's.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

PRESENCE_DOCTYPES = (
	"Presence Settings",
	"Presence Till",
	"Presence Device",
	"Employee Device",
	"Presence Sighting",
	"Presence Session",
)


class TestPresencePermissions(FrappeTestCase):
	def test_system_manager_can_read_and_write_every_presence_doctype(self):
		"""The owner path. System Manager is what an owner actually holds."""
		for doctype in PRESENCE_DOCTYPES:
			for ptype in ("read", "write", "create", "delete"):
				with self.subTest(doctype=doctype, ptype=ptype):
					roles = frappe.get_all(
						"Custom DocPerm",
						filters={"parent": doctype, ptype: 1, "permlevel": 0},
						pluck="role",
					) or frappe.get_all(
						"DocPerm",
						filters={"parent": doctype, ptype: 1, "permlevel": 0},
						pluck="role",
					)
					self.assertIn(
						"System Manager",
						roles,
						f"System Manager has no {ptype} on {doctype} - owners will 403",
					)

	def test_a_cashier_bundle_carries_no_presence_access(self):
		from barakat.permissions import bundle_for

		cashier_roles = set(bundle_for("Cashier"))
		for doctype in PRESENCE_DOCTYPES:
			with self.subTest(doctype=doctype):
				allowed = set(
					frappe.get_all(
						"Custom DocPerm",
						filters={"parent": doctype, "read": 1, "permlevel": 0},
						pluck="role",
					)
				)
				self.assertEqual(
					allowed & cashier_roles,
					set(),
					f"Cashier can read {doctype}",
				)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> run-tests --module barakat.presence.test_permissions
```

Expected: the System Manager test FAILS for at least the doctypes whose persona perms Task 6 generated, because generated persona perms replace the JSON's own permission block.

If it passes immediately, the doctype JSON `permissions` blocks are still intact — record that and still add the grant below, because the generated perms overwrite them on the next `after_migrate`.

- [ ] **Step 3: Write minimal implementation**

Add to `barakat/setup/install.py`:

```python
PRESENCE_DOCTYPES = (
	"Presence Settings",
	"Presence Till",
	"Presence Device",
	"Employee Device",
	"Presence Sighting",
	"Presence Session",
)


def grant_owner_presence_perms():
	"""Give System Manager full access to every presence doctype.

	Owner accounts hold System Manager and no persona, and reach ERPNext under their
	own native roles. Without this, a capability that exists only on a Barakat persona
	role is invisible to them: the screen renders, the action 403s, and only for the
	owner. Here that would mean a shop owner cannot open his own staff's attendance.

	Idempotent, and wired into after_install AND after_migrate - persona perms are
	regenerated on migrate and would otherwise drop this every deploy.
	"""

	from frappe.permissions import add_permission, update_permission_property

	for doctype in PRESENCE_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		add_permission(doctype, "System Manager", 0)
		for ptype in ("read", "write", "create", "delete"):
			update_permission_property(
				doctype, "System Manager", 0, ptype, 1, validate=False
			)
```

Then call it from both entry points. In `after_install` and `after_migrate`, add:

```python
	grant_owner_presence_perms()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> migrate && sudo -u frappe bench --site <local-site> run-tests --module barakat.presence.test_permissions
```

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add barakat/setup/install.py barakat/presence/ && git commit -m "feat(presence): owner-side System Manager grant on presence doctypes"
```

---

### Task 8: Full suite, version bump, push

**Files:**
- Modify: `barakat/__init__.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `barakat` at version `4.9.0`.

- [ ] **Step 1: Run every test that this plan touched or could break**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && python -m unittest barakat.presence.test_engine -v
```

Then on the bench:

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> run-tests --app barakat
```

Expected: everything passes. A failure in a test this plan did not touch is a regression from this plan — fix it before continuing, do not skip it.

- [ ] **Step 2: Bump the version**

In `barakat/__init__.py`, change `__version__ = "4.8.0"` to `__version__ = "4.9.0"`.

Minor, not patch: this adds six doctypes and a new permission surface. It is a new capability, not a fix.

- [ ] **Step 3: Confirm nothing changed for a company that did not ask for this**

```bash
cd /home/frappe/erp_project && sudo -u frappe bench --site <local-site> console
```

```python
from barakat.presence.mode import is_wifi_mode
for company in frappe.get_all("Company", pluck="name"):
	print(company, is_wifi_mode(company))
```

Expected: `False` for every company. If any prints `True`, a settings row was created that should not exist — find out why before pushing.

- [ ] **Step 4: Commit and push**

```bash
git add barakat/__init__.py && git commit -m "chore(release): barakat 4.9.0" && git push upstream dev
```

- [ ] **Step 5: Confirm the push landed**

```bash
cd /c/Users/IzTech-OTbaileh/Desktop/barakat-qa/barakat-dev && git fetch upstream && git log --oneline -1 upstream/dev && git status --short
```

Expected: `upstream/dev` is at the release commit and the tree is clean. **Do not promote to `test` or `main`** — nothing in this plan is usable yet, and the POS release process bakes in whatever the shared worktree holds.

---

## Self-review

**Spec coverage for steps B and C:**

| Spec section | Task |
|---|---|
| §8.1 merging tills | 2 |
| §8.2 arrivals immediate | 1 |
| §8.3 departure wait, real vanish time, flapping | 2 |
| §4.3 warm-up | 3 |
| §4.4 blind vs empty (engine half) | 3 |
| §3 mode switch, off by default | 4 |
| §7.1–7.6 data model | 4, 5 |
| §10.8 tenant boundary, mandatory company | 5, 6 |
| §10.7 persona gates | 6 |
| §10.10 owner blind spot | 7 |

**Deliberately not in this plan**, and each has a home:

- `Presence Pairing Session`, `Presence Alert` → plans 4 and 5, with the features that use them.
- The permlevel-1 role-drop trap → plan 2, where a user is first created.
- The watcher's zero-permission user → plan 2, where it is first created.
- §14.4 security tests 1–8 and §14.2b HRMS tests → plans 2 and 5; nothing here has an endpoint or writes a check-in.
- `frappe.get_list` vs `get_all` → plan 2, the first read path. Note that `test_permissions.py` above uses `get_all` deliberately: it is auditing the DocPerm table itself, which is exactly the case where ignoring permissions is correct.

**Open question for the implementer, answer before Task 5:** the local bench site name. Every bench command above says `<local-site>`. Get it from the `barakat-local` skill's `sites` command rather than guessing.
