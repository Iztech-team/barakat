# Staff presence and attendance — full specification

Date: 2026-08-11
Status: design approved, not built
Companion: `./2026-08-11-staff-presence-plain-words.md` — the same system in plain words with no detail.

**Revision 2 (2026-08-11)** — four changes from revision 1, all owner decisions:

1. **Wifi only.** Nothing reads POS shifts, logins or invoices. Removed everywhere.
2. **Inside the `barakat` app**, not a separate Frappe app.
3. **A per-company switch**: keep attendance exactly as it is today, or turn on wifi
   presence. Manual editing of attendance stays available in both modes.
4. Full till enrollment and key mechanism, with the Admin Panel screens for it.

---

## 1. What this is

Staff arrive at a branch. Their phone joins the shop wifi. They are marked present. They
leave, the phone goes away, they are marked gone. Attendance lands in ERPNext with no
button pressed by anybody.

### 1.1 Goals

1. Attendance recorded automatically, with no action from staff.
2. Works on any router, of any brand, at any price. We never talk to the router.
3. Off by default. A company that does nothing sees no change at all.
4. A human can always correct any record, in either mode.
5. One client's data can never be seen by another.
6. A stolen till gives an attacker almost nothing.

### 1.2 Non-goals — tell the client before building

- **Not a time clock.** Accuracy is roughly ±15 minutes. Anyone needing second-accurate
  punches needs a fingerprint device.
- **Not proof a person is present.** It proves a *phone* is present. A phone left in a
  drawer looks like a person at work. There is no fix.
- **Not zero-touch on every network.** Some cheap routers block devices from seeing each
  other and need one setting changed by hand.
- **Not a surveillance product.** It is an attendance convenience.

### 1.3 What wifi-only costs us, stated once

Revision 1 also read POS shifts and invoices as hard evidence of presence. That is now out
of scope, by decision. The consequences, so nobody is surprised later:

| Lost | Replacement |
|---|---|
| Attendance working before any watcher is deployed | Nothing ships until the watcher ships. There is no earlier milestone. |
| A hard anchor when the wifi reading is wrong | The manager corrects the record by hand. |
| Automatic detection of *which* new phone is someone's | We detect that a pairing has **stopped working** and ask the manager to re-pair. 20 seconds. |
| Cover for a flat battery or someone on mobile data | Nothing is recorded. The manager fills it in. |

This is workable because requirement 4 — manual editing always available — is the safety
net. It is a person instead of a second signal.

### 1.4 Words used here

| Word | Meaning |
|---|---|
| **watcher** | small background program on a till PC that looks at the shop network |
| **device id** | the network id a phone announces. Modern phones randomise it. |
| **sighting** | one observation: at this time, this branch could see this device |
| **presence session** | the decision: this employee was at this branch from X to Y |
| **pairing** | the record connecting a device id to an employee |

---

## 2. Where everything lives

| Piece | Runs on | Repo | New? |
|---|---|---|---|
| Watcher | till PC, Windows background service | `barakat-electrobun`, shipped by the POS installer | **new** |
| Presence engine, doctypes, API | the ERPNext bench, **inside the `barakat` app** | `barakat` | **new code, existing app** |
| Settings, tills, pairing, alerts screens | Admin Panel | `admin_panel_barakat` | extend |
| API passthrough | proxy | `proxy-barakat` | extend |
| Attendance records | ERPNext / HRMS `Employee Checkin` | — | exists |

Presence code lives under `barakat/presence/` — its own folder, its own doctypes, its own
tests. It ships and versions with `barakat` and needs no separate release process.

### 2.1 petromall

`petromall` is not ours and already has the `barakat` app installed. Putting presence
inside `barakat` means its doctypes will exist on that site.

**This is safe only because the feature is off unless switched on per company.** Nothing
runs, nothing is scheduled, no endpoint accepts anything, until a company is switched on.
`petromall` is never switched on. The scheduled jobs in §9 and §10 must iterate over
**enabled companies**, never over all companies and never over all sites.

### 2.2 The one rule of the architecture

> **The watcher sees. The engine decides.**

The watcher holds no thresholds, no timers, no matching and no identity logic. There will
be dozens of watchers in the field that are slow to update; there is one engine that can be
fixed in minutes. Every time this rule was bent during design it produced a bug.

### 2.3 What HRMS already does — do not rebuild any of it

Researched 2026-08-11 against the Frappe HR documentation. This section exists because
almost everything downstream of a check-in is already built, tested and maintained by
somebody else.

**We create `Employee Checkin` records. That is the entire integration. Nothing else.**

Everything in this table is HRMS's job and must not be reimplemented:

| Already exists | Where |
|---|---|
| Raw punch record with `log_type` IN/OUT, `shift`, `device_id`, `skip_auto_attendance`, geolocation | `Employee Checkin` |
| Turning punches into attendance, hourly, automatically | `Shift Type` → *Enable Auto Attendance* |
| Working-hours calculation, two modes (first-in/last-out, or every valid pair) | `Shift Type` |
| Half-day threshold | `Shift Type.working_hours_threshold_for_half_day` |
| Absent threshold | `Shift Type.working_hours_threshold_for_absent` |
| Late entry and early exit, with grace periods | `Shift Type` |
| Pairing IN with OUT — alternating, or strictly by log type | `Shift Type.determine_check_in_and_check_out` |
| Not marking people absent on holidays | Holiday List: Shift Type → Employee → Company default |
| Leave handling | `Leave Application` |
| Who works which shift | `Shift Assignment`, or the employee's default shift |
| The attendance record itself, editable by hand | `Attendance` |
| Manual marking and bulk correction | Employee Attendance Tool, `Attendance Request` |
| A device id field on the employee | `Employee.attendance_device_id` |
| A worked reference implementation of pushing punches in | `frappe/biometric-attendance-sync-tool` |

**We build only what nobody else has: turning wifi sightings into IN and OUT punches.**

#### 2.3.1 Three integration facts that will silently break this

**`last_sync_of_checkin` must be updated, or nothing happens.** HRMS only processes a
shift's check-ins once this timestamp on the Shift Type has passed the shift's end time.
Whoever creates the check-ins is responsible for moving it. If we never touch it, we will
create thousands of perfect check-ins and **no attendance will ever be marked**, with no
error anywhere.

Either we advance it ourselves after each write, or we switch on *Automatically update Last
Sync of Checkin* on the Shift Type. **Decide this explicitly and test it — it is the single
most likely way this project silently does nothing.**

**`determine_check_in_and_check_out` must be set to "Strictly based on Log Type".** We
always set `log_type` deliberately. On the alternating setting, one stray punch shifts every
later pair for the rest of the day.

**Auto attendance only processes employees who have a shift.** No `Shift Assignment` and no
default shift means no attendance is even attempted. So the client must set up Shift Types
and assign staff to them **before any of this produces anything** — that is real setup work
on their side, not ours, and it must be in the quote and the timeline.

#### 2.3.2 The default that would have cost somebody money

Straight from the HRMS documentation:

> *In case of absence of any checkin log, attendance will still be marked as Absent.*

The absent threshold can be set to zero to stop absence being decided by working hours —
but **a day with no check-ins at all is still marked Absent**, and that is not configurable.

With wifi as our only signal, "no check-ins at all" happens for ordinary reasons: a flat
battery, someone on mobile data, a phone left at home, a blind branch, a dead till.

**So a phone problem becomes an absence, and an absence becomes lost pay for a real
person.** Revision 2 of this spec claimed a day with no data produces no record. That was
wrong — it is true of *our* code and false of the system our code feeds.

This is now a decision the client has to make with their eyes open. See §8.5.

#### 2.3.3 An alternative worth naming to the client

Frappe HR's mobile app already has check-in with location, today, for free. Staff open the
app and tap a button.

We are not building that because the client asked for something requiring **no action from
staff**. But he should be told it exists, so choosing the harder path is deliberate rather
than accidental.

---

## 3. The mode switch

### 3.1 Two modes, per company

| Mode | Behaviour |
|---|---|
| **Manual** (default) | Exactly what happens today. No watcher accepted, no endpoint, no jobs, no screens beyond the settings page. |
| **Wifi** | Everything in this document is live for that company. |

The switch lives in `Presence Settings`, a per-company doctype, surfaced in the Admin Panel
at `Settings → Attendance`.

### 3.2 What the switch actually controls

Turning it on: registers the scheduled jobs for that company, starts accepting reports from
that company's tills, and reveals the presence screens in the Admin Panel.

Turning it **off**: stops accepting reports, stops creating check-ins, hides the screens.
**Nothing is deleted.** Pairings, sessions and history stay. Turning it back on resumes
where it left off. A switch that destroys data is a switch nobody dares use.

### 3.3 Manual editing, in both modes

**In both modes, a manager can add, edit and delete attendance records exactly as they can
today.** Wifi mode adds a source of records; it never takes away the ability to correct
them.

Every automatic record is marked with its source so a human can tell what came from a phone
and what came from a person. When a human edits an automatic record, the record keeps its
history: who changed it, when, and what it said before. Nothing is silently overwritten —
otherwise a disputed month is unwinnable.

A manually-created record always wins over the automatic one for the same person, day and
branch. The automatic one is kept and marked superseded, not deleted.

---

## 4. How the watcher sees phones

### 4.1 Method

The watcher never contacts the router. It calls out to every address on the shop's local
network and records which answer. This works because answering the lowest-level "who is at
this address" question is not optional for a device on a network.

Verified on a real network 2026-08-11: 254 addresses swept in **0.90–1.35 seconds**, 31
devices found. Only 11 of those 31 replied to the friendly ping — the other 20 were found
anyway by the lower-level answer. **Relying on the friendly reply alone would have missed
two thirds of the shop.**

Sweep time does not depend on how many staff there are. You are asking addresses.

### 4.2 Loop

| Setting | Default | Notes |
|---|---|---|
| Sweep interval | 2 s | local only, never touches the server |
| Warm-up | 60 s | after start, report arrivals only, never departures |
| Report on change | immediate | rate-limited to at most 1 request / 2 s |
| Heartbeat when nothing changed | 30 s | proves the branch is alive |
| Max devices per report | 512 | hard cap; exceeding it is an alarm, never a silent truncation |
| Offline queue | 6 hours | oldest dropped beyond that |

**Scanning is free. Reporting is not.** They are deliberately separate rates. This is why
no "scan faster" command exists — the watcher is always fast, so nothing ever needs to be
sent down to it.

### 4.3 Warm-up is not optional

When a till reboots the watcher knows nothing. Without warm-up it would report every member
of staff as having left. For the first 60 seconds it may report arrivals but never a
departure.

### 4.4 Self-test — "empty" versus "blind"

Every sweep the watcher checks whether it can see the router itself.

| Observation | Meaning | Reported |
|---|---|---|
| Router visible, other devices visible | normal | healthy |
| Router visible, zero other devices | genuinely empty shop | healthy, empty |
| **Router not visible** | isolation on, or wrong network | **blind** |

A blind branch shows red as *unreachable*, never as *nobody came to work*. This is the
difference between failing loudly and lying quietly.

### 4.5 What the watcher never does

- never contacts the router
- never decides anything
- never stores presence, names or employees
- never updates itself — the POS updater does that, signed

**There is no command channel.** The only value it reads from a response is
`next_heartbeat_s`: one integer, range-checked, whose only possible effect is how long it
waits before speaking again. It cannot cause the watcher to run, fetch, install or change
anything.

The day a watcher can be told to fetch and execute something, we have installed a remote
control on every till our clients own, and we would not find out until it was used.

---

## 5. Till enrollment and keys

This is the part the manager actually operates, so it gets a section of its own.

### 5.1 The problem

Every till needs its own credential. It must not be typed by a human, must not travel
through anyone's clipboard, must be revocable one till at a time, and the manager must be
able to see what is connected.

### 5.2 The mechanism — join, approve, key

**Nobody ever sees or types a key.** Not the manager, not the installer, not the shop.

1. **Presence is switched on** for the company.
2. **The till asks to join.** The POS is already logged in with a real session. On start,
   if its watcher has no key, the POS calls `presence.api.request_join` over that session
   and sends: its POS Profile, the computer's name, and a fingerprint of the machine.
3. **A pending record appears.** The engine creates a `Presence Till` in state `pending`.
   It reads the branch **from the POS Profile** — never from anything the till sends. No
   key is issued yet.
4. **The manager approves it** in the Admin Panel, seeing branch, till and computer name.
   One click.
5. **The key is delivered.** On its next join call (every 30 s while unapproved), the POS
   receives the key once and writes it to the Windows credential store, not to a file.
   It is never returned again.
6. **The watcher starts reporting.**

### 5.3 Why the approval step exists

Without it, any POS login could register a till. With it, a human has to recognise the
machine name and the branch before anything is accepted.

It costs one click per till, once, forever. A branch with three tills is three clicks in
its whole lifetime.

### 5.4 Key lifecycle

| Event | Behaviour |
|---|---|
| Issued | once, at approval, delivered to the POS over its authenticated session |
| Stored | Windows credential store, marked non-exportable. Never a file beside the executable. |
| Rotated | automatically every 90 days, over the same channel, no human involved |
| Rotation missed | key expires on its own. A forgotten till closes its own door. |
| Suspended | one click. Next request fails. Record and history kept. |
| Reissued | one click, for a reimaged or replaced PC. Old key dies instantly. |
| Retired | till decommissioned; user disabled; history kept |
| Seen from two places at once | alarm. A till exists in one place. |

### 5.5 The Admin Panel screens

**`Settings → Attendance`** — one page, per company:

- Mode: **Manual** / **Wifi presence** (default Manual)
- The timings from §7.6, with plain-language labels and their defaults
- A plain sentence stating what turning it on means, and that manual editing stays available

**`Settings → Attendance → Tills`** — the list:

| Column | Shows |
|---|---|
| Branch | from the POS Profile |
| Till | POS Profile name |
| Computer | machine name reported at join |
| Status | pending / active / suspended / **blind** / **unreachable** / retired |
| Last seen | how long ago it spoke |
| Version | watcher version, flagged if far behind |

Row actions: **Approve** (pending only), **Suspend**, **Reissue key**, **Retire**.

Pending tills and any branch that is blind or unreachable also raise a card on the Admin
Panel home. A thing that arrives at you, not a thing you go looking for.

**`Staff → <employee> → Devices`** — the pairing screen in §8.

### 5.6 What the manager never sees

The key itself. There is nothing to copy, nothing to paste, nothing to leak in a WhatsApp
message, and nothing to get wrong.

---

## 6. The API

Two endpoints. One writes, one enrolls. Neither reads presence.

```
POST /api/method/barakat.presence.api.request_join    (POS session auth)
POST /api/method/barakat.presence.api.report          (till key auth)
```

### 6.1 Report request

```json
{
  "sent_at": "2026-08-11T07:58:04+03:00",
  "seq": 10432,
  "watcher_version": "1.0.0",
  "health": { "blind": false, "gateway_seen": true, "sweep_ms": 1180 },
  "devices": [ { "id": "aa1122334455" }, { "id": "bebe0df05ab8" } ]
}
```

The body carries **no company, no branch, no till**. All three come from the key. A caller
that can name its own scope can name someone else's.

### 6.2 Report response

```json
{ "ok": true, "server_time": "2026-08-11T07:58:05+03:00", "next_heartbeat_s": 30 }
```

### 6.3 Rules

| Rule | Behaviour |
|---|---|
| Company not in wifi mode | rejected. The endpoint does not exist for that company. |
| `seq` not greater than the last seen for this till | dropped, counted, alarm at 10/hour |
| `sent_at` more than 5 minutes from server time | accepted, clock-drift alarm raised |
| More than 512 devices | rejected, alarm |
| Sustained over 1 request/second | rate limited, alarm |
| Body over 64 KB | rejected |
| Till not `active` | rejected |

### 6.4 Time

**The server stamps every event on arrival. `sent_at` is never used in a calculation.** Till
PCs have wrong clocks routinely and a wrong clock would silently corrupt attendance.
`sent_at` is kept for support and for the drift alarm only.

### 6.5 Writing without the heavy machinery

Sightings are written with direct SQL inserts, not through the document layer, which runs
hooks, versioning and permission checks per row — correct for things humans touch, far too
heavy for a constant stream.

Everything a human touches — pairings, settings, tills, check-ins, alerts — goes through
the normal document layer and gets the permissions and history that come with it.

---

## 7. Data model

All doctypes live in `barakat/presence/`.

### 7.1 `Presence Settings` — per company
`company`, `mode` (manual / wifi), `departure_wait_minutes` (15), `sweep_interval_s` (2),
`heartbeat_s` (30), `warmup_s` (60), `sighting_retention_days` (30),
`pairing_timeout_s` (120), `rot_days` (7), `max_devices` (512).

**Nothing tunable lives in code.** Changing behaviour on the bench needs a pull and a
restart by hand on production; changing a setting does not. Anything we expect to fiddle
with is a setting.

### 7.2 `Presence Till`
`pos_profile`, `company`, `branch`, `machine_name`, `machine_fingerprint`, `api_user`,
`status`, `last_seen`, `last_seq`, `watcher_version`, `last_clock_drift_s`,
`approved_by`, `approved_at`.

**Branch is read from the POS Profile and never typed in.** A mislabelled till puts one
person in two places and makes their attendance nonsense.

### 7.3 `Presence Device`
`company`, `device_key`, `raw_id`, `display_suffix`, `is_randomised`, `first_seen`,
`last_seen`, `days_seen`.

**`device_key` is always a keyed hash** of the raw id with a per-company key. It is the only
identifier used anywhere else — sightings, pairings, logs. Nothing but this table holds a
raw id.

**`raw_id` is filled only for devices paired to an employee**, and cleared when the last
pairing closes. Customer phones pass through these shops all day; a raw list of them is a
liability with no upside.

`display_suffix` is the last four characters, kept for every device so the pairing screen
can show a human two rows apart. Four characters identify nothing on their own.

### 7.4 `Employee Device` — the pairing
`employee`, `device_key`, `company`, `valid_from`, `valid_to`, `paired_by`, `notes`.

1. **Never deleted, closed with `valid_to`.** Deleting the phone someone used in January
   makes January's attendance impossible to explain.
2. **One employee may have many devices.** Phone plus tablet plus an old phone in a drawer.
   Present if *any* is present. A one-device-per-person table fails in month two.
3. A device may belong to only one employee at a time.

### 7.5 `Presence Sighting` — raw, short-lived
`company`, `branch`, `till`, `device_key`, `event` (appeared / gone), `server_time`.

**Only changes are written, never the full list.** A branch storing every sweep would write
around 43,000 rows a day; storing only changes makes the same day roughly 60 rows.

Deleted after 30 days.

### 7.6 `Presence Session` — the answer
`company`, `branch`, `employee`, `in_time`, `out_time`, `checkin_in`, `checkin_out`,
`state` (open / closed / superseded).

### 7.7 `Presence Pairing Session`
`employee`, `branch`, `started_by`, `started_at`, `baseline_devices`, `state`,
`candidate_device`, `expires_at`.

### 7.8 `Presence Alert`
`company`, `branch`, `kind`, `severity`, `opened_at`, `closed_at`, `detail`. Alerts close
themselves when the condition clears.

---

## 8. The engine

### 8.1 Merging several tills

A busy branch has three tills, so three watchers reporting the same phones.

**The branch is the unit of truth, not the till.** Store "Ramallah saw this device", never
"till 2 saw this device".

- Present if **any** watcher at the branch sees it.
- Gone only when **no** watcher at the branch has seen it for the wait period.

Not a vote, not an average. One eye in the room is enough. This gives redundancy free: two
tills can be off and the branch still records attendance. With a single watcher, one
switched-off PC means the branch records nobody at work all day.

### 8.2 Arrival

A paired device appears and its employee has no open session at that branch → open a
session immediately.

**Arrivals are never delayed.** Appearing is trustworthy; disappearing is not.

### 8.3 Departure — the wait

The failure this exists to prevent: a phone screen sleeps and the phone quietly drops off
the wifi while its owner is still at work.

1. The last watcher at the branch loses the device → start a timer, change nothing.
2. The device returns before the timer ends → cancel the timer. **No event is recorded at
   all.** The session is untouched; the person was never away.
3. The timer ends with the device still missing → close the session with `out_time` set to
   **when it actually vanished**, not when the timer finished.

Default wait 15 minutes, from settings. **This number is a guess until field test 13.5.2
measures it.**

### 8.4 Writing to ERPNext — the whole integration

Session opens → create `Employee Checkin`, `log_type = IN`.
Session closes → create `Employee Checkin`, `log_type = OUT`.

**That is everything we write. See §2.3 — all the rest is HRMS's job.**

| Field | Value |
|---|---|
| `employee` | from the pairing |
| `time` | the session time, server-stamped |
| `log_type` | IN or OUT, always set explicitly |
| `device_id` | `presence:<branch>` — free traceability, no new field needed |
| `shift` | left to HRMS to resolve on save |

Both writes are idempotent, keyed on the session, so a retry can never double-write. The
resulting names are stored back on the session.

After each write, **advance `last_sync_of_checkin`** on the relevant Shift Type, or confirm
*Automatically update Last Sync of Checkin* is switched on. Without this, no attendance is
ever marked and nothing anywhere reports an error (§2.3.1).

Every automatic check-in is identifiable as machine-created by its `device_id`. Corrections
are new records with a reason and an author; nothing is edited over. A disputed month has to
stay answerable a year later.

### 8.5 Days with nothing recorded — the decision the client must make

Our code creates no session and no check-in for a day where nobody was detected.

**But HRMS will still mark that day Absent.** A day with zero check-ins is marked absent and
that part is not configurable (§2.3.2). So the honest statement is: *we* record nothing, and
the system we feed records an absence.

The ordinary causes are a flat battery, mobile data, a phone left at home, a blind branch or
a dead till. None of them mean the person did not come to work.

Three ways to handle it. **The client picks, in writing, before check-in creation is turned
on:**

| Option | What happens | Cost |
|---|---|---|
| **A. Auto attendance off** | We create check-ins; a manager marks attendance from them. | A daily human job. Nothing is ever wrong by accident. |
| **B. Auto attendance on, plus a daily "no data" list** | Runs itself. Each morning the manager gets yesterday's staff with no data and fixes them. | Fast, but wrong until somebody looks. |
| **C. Auto attendance on, unattended** | Fully automatic. | A flat battery costs a real person a day's pay. **Not recommended.** |

The **no-data list is built either way** — it is the same query, and option B is worthless
without it.

Absence is a decision about a person's pay. This system is not entitled to make it silently,
and we are not entitled to let it happen by never asking.

---

## 9. Pairing

### 9.1 Instant pairing — the only way in

**Where:** Admin Panel, `Staff → <employee> → Devices → Pair phone`. Manager role only.

**Not on the till.** Pairing is exactly how someone would cheat this — pair a friend's
phone as yourself, leave it in the shop, go home. And the Admin Panel ships in minutes;
the till app takes a build and an update wave across every shop.

| Time | What |
|---|---|
| 0:00 | Manager clicks **Pair phone**. Engine snapshots every device visible at that branch — the baseline. |
| 0:02 | Screen: *"Ask Ahmad to turn his wifi off."* |
| 0:03 | Ahmad turns wifi off. |
| 0:04 | A device in the baseline is gone. Screen: *"Found it — …5A-B8. Confirm?"* |
| 0:06 | Manager taps confirm. Saved. |

**Six seconds.** The remaining delay is a human reaching for a phone, not software.

**Only the disappearance is needed.** The moment the phone vanishes we know which one it
is. The return is used only to break a tie.

**The manager never types a device id.** He can only tap something the system found. That
is what stops a phone being paired that was never in the room.

### 9.2 Pairing edge cases — all of them

| Situation | Behaviour |
|---|---|
| Nothing disappeared before the timeout | *"We didn't see anything leave. Is his phone on the shop wifi rather than mobile data?"* |
| **Two or more** devices disappeared | *"Two phones did that. Ask him to turn his back on."* The one that returns is his. |
| Still ambiguous after the tie-break | Offer both with their history. Never guess silently. |
| Device returns with a **new id** | If exactly one left and exactly one never-before-seen device appeared in the same window, treat it as the same phone with a new face. The manager is not told; nothing odd happened from their point of view. |
| Device already paired to someone else | Blocking warning naming that person. Move, or cancel. Never silent. |
| Device already paired to the same person | *"Already paired"*, no duplicate row. |
| Employee already has 5 devices | Warn, do not block. Five is a smell. |
| Manager walks away | Session expires after 2 minutes and is discarded. |
| Two managers pairing at one branch | One session per branch. The second waits. |
| Branch is blind | *"We can't see this branch right now"* — never *"no phone found"*. |
| Employee has no `Employee` record | Pairing is not offered. |

### 9.3 Detecting a rotted pairing

A pairing dies quietly: a new phone, or someone taps "forget this network" and their phone
rejoins wearing a new id. Nobody reports it.

Without POS evidence we cannot work out *which* new device is theirs. We can reliably work
out that the old one **stopped working**:

> An employee has an active pairing, other staff at the same branch are being detected
> normally, and this employee's device has not been seen for `rot_days` (default 7).

That raises a card: *"Ahmad hasn't been detected for 7 days, but the branch is working
normally. Has his phone changed?"* → **Re-pair** (the 6-second flow) or **Dismiss**.

The branch-is-working check matters. Without it, every holiday and every closed week would
raise cards for the whole team.

### 9.4 Unpairing
Closes the row with `valid_to = now`. History intact. Presence from that device stops
counting forward.

---

## 10. Security

### 10.1 The honest starting point

Presence lives inside ERPNext, and ERPNext is **already reachable from the internet**
because the tills and the Admin Panel need it. The "invisible from the street" option died
when the engine moved onto the bench. That was the price of removing the second database.
Worth paying — but stated plainly rather than glossed over.

The protection is not *hide the door*. It is: **make the key worthless, the account empty,
and the door accept exactly one sentence.**

### 10.2 The credential

Each till gets **its own ERPNext user and its own key**. Never one shared key: a shared key
leaks once and every shop of every client is exposed forever, with no fix short of
redeploying the world.

**The watcher must never use the POS's login.** The POS user can read customers, prices and
invoices. A stolen watcher key must not be a stolen till account.

The watcher's user has exactly one permission: call `report`. It cannot read staff,
invoices, customers, settings, other branches, or its own reports. Logged in as it, there is
nothing to look at.

### 10.3 Scope comes from the credential

The body carries no company, branch or till. The key already answered all three. There is
no code path in which a caller supplies its own scope.

This is the rule that failed once already on Contact and Item Price. One enforcement point,
never a filter the caller provides.

### 10.4 What a stolen key gets you

Assume the worst. Someone with physical access to a till extracts its key. They can send
**false device lists for that one till, at that one branch, of that one client**.

They cannot read anything, cannot see other branches or clients, cannot reach invoices,
customers or money. Response is one click.

Worst realistic damage: one branch's attendance is wrong for a while, visible afterwards
because sightings are logged, and correctable by hand because manual editing always works.

### 10.5 Threat table

| Threat | Result |
|---|---|
| Internet-wide scanning finds the endpoint | needs a valid key; without one there is nothing but a rejection |
| Stolen till key | §10.4 |
| Replay of old requests | `seq` must increase per till; stale dropped and counted |
| Flood from a valid key | rate limited, alarmed, body size capped |
| Any POS login registering a till | creates a `pending` record only. No key without a manager's click. |
| Customer on the shop wifi copies a staff device id | possible in principle; ids rotate, and the damage is a wrong attendance record a manager can correct |
| Cashier pairs a friend's phone | cannot reach the screen or the endpoints behind it |
| Manager fakes attendance | possible, and logged with a name, a time and a reason. Nothing overwritten. |
| Engine compromised | can create wrong check-ins; cannot invent employees; every record is attributable and correctable |
| One client's data reaching another | each client is a separate site with a separate database. Structural, not filtered. |
| Presence touching `petromall` | never switched on there; every job iterates enabled companies only |

### 10.6 Stronger door — deferred, not forgotten

Presence traffic can be moved to its own entrance that refuses any connection without a
certificate we issued, dropping unknown callers before they reach any code. Extra work, and
needs the bench's web server configuration handled carefully. Not in the first release.
Recorded so the option is not lost.

### 10.7 Roles and permissions — reuse the model, do not invent one

Checked against the live code 2026-08-11 (`barakat/persona_matrix.py`,
`barakat/overrides/company_scope.py`, `proxy-barakat/src/modules/roles/catalog.ts`).

**`attendance` is already a permission module.** `MODULE_DOCTYPES["attendance"]` maps to
`("Attendance",)`, with `("Employee", "Branch")` as related links, and personas already
carry it: **Manager and HR have `attendance: write`; everyone else has `none`.**

So presence hangs off the existing modules. **No new module key is invented** — that would
mean a new column in the persona matrix in two repos, kept in sync by hand, for no gain.

| Thing | Module gate | Who that is today |
|---|---|---|
| View presence, sessions, no-data list | `attendance: read` | Manager, HR |
| Pair / unpair a device | **`staff: write`** | **Manager only** — HR is `staff: read` |
| Approve, suspend, reissue a till | `settings: write` | Manager |
| Turn wifi mode on or off | `settings: write` | Manager |
| Everything above, for a Cashier | — | nothing. All four are `none`. |

Pairing sits on `staff: write` rather than `attendance: write` deliberately: pairing is the
one action that can be used to commit fraud, and `staff: write` is the narrower gate — it is
what already restricts creating logins to the Manager persona.

### 10.8 The tenant boundary — the trap that already bit once

`Contact` and `Item Price` leaked across tenants on production until barakat 4.4.0. The rule
from that fix applies directly here:

> **The boundary is one `Company` User Permission, and Frappe binds it by walking the
> doctype's Link fields. A doctype with no `Link → Company` field is returned completely
> unscoped. And a BLANK company value is visible to everyone.**

We own these doctypes outright, so this is easier than it was for `Contact` — but only if
done deliberately:

1. **Every presence doctype carries a native `company` field, `Link → Company`, `reqd = 1`.**
   Mandatory means it can never be blank, which removes the blank-marker half of the bug at
   the schema level rather than with a hook and a backfill patch.
2. **Every one is classified in `company_scope.py`** — none is company-neutral.
   `test_company_scope.py` fails until a newly guarded doctype is decided either way, so
   this cannot be forgotten. Let it fail first; that is the test doing its job.
3. **Company is derived, never accepted.** It comes from the till's key, or from the
   `Employee`. The `X-Barakat-Company` header is not a source — it is caller-controlled and
   a stamp is a lasting write.
4. **`frappe.get_list`, never `frappe.get_all`,** in any presence query. `get_all` ignores
   permissions and would "prove" the boundary works on a broken one.

### 10.9 The watcher's user needs zero permissions

Sightings are written with direct SQL (§6.5), which bypasses the permission system entirely.
That sounds alarming and is actually the tightest available shape:

**The watcher's user gets no DocPerm on any doctype at all.** Not read, not write, not on
presence tables, not on anything. Its only capability is that it may call one whitelisted
method.

Which means **the method itself is the entire security boundary**, and it must:

- resolve company, branch and till **from the key**, never from the body
- refuse if the till is not `active`
- refuse if the company is not in wifi mode
- never read or return anything

A whitelisted method is callable by any authenticated user unless it checks. This one checks
that the caller is a registered active till, first line, before anything else.

### 10.10 Four known traps in this codebase that this feature will walk into

Each has cost real time before. They are listed so they cost none this time.

**The permlevel-1 trap.** `User.roles` is permlevel 1, writable only by System Manager.
`add_roles()` ends in a plain `save()`, so when a **Manager** approves a till and we create
its user, Frappe **silently drops the role rows** — no error, HTTP 200, and a watcher that
can never authenticate. Any role assignment on behalf of another user must rewrite the child
table and `save(ignore_permissions=True)`.
*Test:* approve a till **as a Manager through the Admin Panel**, then read `tabHas Role`
directly. Do not trust the 200.

**The owner blind spot.** Owner accounts hold `System Manager` and no persona, and hit
ERPNext under their own native roles. Any capability that exists only on a `Barakat *` role
is invisible to them — the screen renders and the action 403s, for the owner only. If
presence doctypes are permed only via a persona role, **the shop owner cannot see his own
staff's attendance.**
*Fix:* grant System Manager full access to the presence doctypes in `setup/install.py`,
wired into `after_install` **and** `after_migrate`.

**The POS blind spot.** The desktop POS wraps every sync in try/catch and falls back to
defaults, so a missing DocPerm looks like "not configured" forever, silently. **The watcher
has exactly this shape.** A permission problem will not surface as an error anywhere.
*Rule:* never test watcher permissions by running the watcher. Audit the `DocPerm` /
`Custom DocPerm` rows directly and intersect with the persona bundle.

**Proxy route coverage.** `assertRouteCoverage` throws at boot if any registered route has
no `ROUTE_PERMISSIONS` rule. Every new presence route needs its entry in the same commit or
the proxy will not start. This is a feature — it is what stops an endpoint shipping ungated.

### 10.11 Permission tests — persona by persona

Added to §14.4. Run for **Cashier, Branch Supervisor, Inventory Keeper, Accountant, HR,
Manager, and an owner account**, because owners fail differently from everyone else.

1. Cashier: every presence read, every pairing endpoint, the tills screen, the mode switch
   → all rejected.
2. HR: can read presence and the no-data list. **Cannot pair a device** (`staff: read`).
3. Manager: can do everything, including approving a till.
4. Owner: can read presence and attendance. Specifically prove this — it is the blind spot.
5. A Manager of company A: cannot see company B's tills, devices, pairings or sessions.
   Compare **seen versus total**, and pair each presence doctype with a control doctype
   known to be scoped (`Customer`) in the same call shape.
6. Every presence doctype queried with a blank company → returns nothing. Then confirm no
   row can be created with a blank company at all.
7. The watcher's user: attempt to read every presence doctype, `Employee`, `Attendance`,
   `Customer` → all rejected. It holds no DocPerm by design.
8. Walk the actual Admin Panel forms per persona, not just the routes. An API sweep proves
   reachability but **cannot see an empty dropdown** — two real breaks in this codebase were
   found only by driving the forms.

---

## 11. Privacy and retention

This holds a minute-by-minute record of where named people were. Treat it that way from day
one; retrofitting is miserable.

| Data | Kept | Then |
|---|---|---|
| Raw sightings | 30 days | deleted by scheduled job |
| Presence sessions | as long as attendance needs them | — |
| Unpaired (customer) devices | keyed hash only, never the raw id | counted, never identified |
| Pairing and alert history | years | never deleted |

No device ids in application logs — log the key, never the raw id. Encrypted at rest and in
transit. The retention job runs for **enabled companies only**.

---

## 12. Alarms

All of these surface on the Admin Panel home, not buried in a report.

| Alarm | Trigger | Severity |
|---|---|---|
| Till waiting for approval | a `pending` record exists | info |
| Branch unreachable | no heartbeat from any till for 5 min | high |
| Branch blind | watcher cannot see the router | high |
| Employee at two branches | same person, two branches, inside one minute | high — always a setup bug |
| Device paired twice | integrity check | high |
| Key seen from two locations | same key, two network origins | high |
| Clock drift | `sent_at` more than 5 min out | medium |
| Sequence violations | more than 10 in an hour | medium |
| Rate limit hit | sustained | medium |
| Device cap exceeded | more than 512 in a report | medium |
| Pairing rotted | §9.3 | low, becomes a card |
| Watcher version old | more than 2 releases behind | low |

---

## 13. Edge cases — the master list

| # | Case | Behaviour |
|---|---|---|
| 1 | Phone sleeps in a pocket | departure timer; return cancels it; no event recorded |
| 2 | Phone flaps repeatedly | each return cancels the timer; at most one session |
| 3 | Shop internet down | watcher queues up to 6 hours and replays; server stamps arrival time |
| 4 | Till PC off | other tills cover; if it was the only one, branch is **unreachable**, never empty |
| 5 | All tills off | branch unreachable; we write nothing; **HRMS marks everyone absent** unless §8.5 option A is chosen. Appears on the no-data list. |
| 6 | Client isolation on | watcher reports blind; same absence consequence as #5 |
| 7 | Staff on mobile data | never seen; we write nothing; **HRMS marks absent**; no-data list |
| 8 | Flat battery mid-shift | looks like a departure; the OUT time is wrong; manager corrects it |
| 8b | Flat battery before arriving | never seen at all; **HRMS marks absent**; no-data list |
| 8c | Employee has no shift assigned | HRMS attempts nothing, absent or otherwise. Our check-ins sit unused. Setup problem, alarmed. |
| 8d | `last_sync_of_checkin` never advances | check-ins pile up, **no attendance is ever marked, and nothing errors**. Alarm if check-ins exist for a shift whose last sync is more than a day behind. |
| 8e | Holiday or approved leave | HRMS already handles both. We do nothing and must not. |
| 9 | New phone | detected as rot after 7 days; manager re-pairs in 6 seconds |
| 10 | "Forget this network" | same as a new phone |
| 11 | Two phones for one person | both paired; present if either is seen |
| 12 | Person leaves the company | pairings closed with a date; history intact |
| 13 | Person moves branch | pairing is per company, not per branch; works immediately |
| 14 | Person covers a shift at another branch | presence follows them; no manual step |
| 15 | Person visits two branches in a day | two sessions, sequential; overlap is the alarm in §12 |
| 16 | Till labelled with the wrong branch | branch comes from the POS Profile; overlap alarm catches the rest |
| 17 | Till reboots mid-day | warm-up prevents a false mass departure |
| 18 | Watcher crashes | Windows restarts it; the gap shows as unreachable |
| 19 | Clock wrong on the till | server time used for everything; drift alarm raised |
| 20 | Duplicate report | `seq` deduplicates |
| 21 | Report out of order | `seq` ordering; stale dropped |
| 22 | Huge device count | capped at 512, alarmed, never silently truncated |
| 23 | Same device at two branches | possible on a shared network; alarm raised, neither session assumed |
| 24 | Employee record missing | device is recorded; no pairing offered; no check-in |
| 25 | Two employees share a phone | second pairing blocked with a named warning |
| 26 | Shift crosses midnight | sessions are not cut at midnight; real timestamps |
| 27 | Holiday, nobody in | branch healthy and empty — distinct from unreachable |
| 28 | Mode switched off mid-day | open sessions are closed at the switch time; nothing deleted |
| 29 | Mode switched back on | resumes; old pairings still valid |
| 30 | `barakat` upgraded mid-day | in-flight reports retried from the watcher's queue |
| 31 | ERPNext down | reports rejected; watcher queues; nothing lost within 6 hours |
| 32 | Manager edits an automatic record | manual wins; automatic kept and marked superseded |
| 33 | Retention deletes sightings while a session is open | sessions do not depend on sightings once opened |
| 34 | Company never switched on | no jobs, no endpoint, no screens. `petromall`'s permanent state. |

---

## 14. Testing

Heavier than usual because the failures are quiet. A wrong attendance record does not throw
an error. It sits there being wrong until payday.

### 14.1 The engine is pure, deliberately

Merging, the departure timer and session opening/closing are **plain functions with no
framework and no database**: observations in, decisions out.

This is not tidiness. It means the entire hard part is testable by feeding it a timeline and
checking the answer, with no bench, no site and no clock.

### 14.2 Unit tests — the engine

1. Simple day: appear 07:58, gone 17:12 → one session, exact times.
2. Pocket sleep: gone 11:30, back 11:36 → **no event at all**, session untouched.
3. Boundary: return at exactly 14:59 and at 15:01 of a 15-minute wait.
4. Flapping: 20 cycles in an hour → exactly one session.
5. Three tills, staggered sightings → one session, union of all three.
6. Two tills disagree permanently → present throughout.
7. Only till 2 sees them and till 2 goes offline → departure only after the wait.
8. `out_time` is when the device vanished, not when the timer finished.
9. Warm-up: a fresh watcher's first report generates no departures.
10. Midnight crossing → one session, not two.
11. Out-of-order reports → correct final state.
12. Duplicate `seq` → ignored.
13. Late sighting after a session closed → does not reopen it.
14. Two devices for one person, one leaves → still present. Both leave → departs after the wait.
15. Employee at two branches within a minute → alarm, no double session.
16. Mode off mid-session → session closed at the switch time, nothing deleted.
17. A day with no sightings → **no session and no check-in**. Not an absence.

### 14.2b HRMS integration tests — prove we did not break or duplicate it

These are the tests that catch "we built something HRMS already had" and "we fed HRMS
something it silently ignored".

1. A session pair produces exactly two `Employee Checkin` records, IN then OUT, with
   `log_type` set explicitly.
2. `last_sync_of_checkin` advances after a write — **or** *Automatically update Last Sync of
   Checkin* is on. Then confirm an `Attendance` record actually appears after the hourly job.
   **This is the test that catches the silent-nothing failure.**
3. Shift Type set to "Alternating entries" → confirm our pairs still land correctly, or
   confirm the setting is enforced as "Strictly based on Log Type" during setup.
4. Employee with no `Shift Assignment` → no attendance attempted; our check-ins exist
   unused; the setup alarm fires.
5. Employee on an approved leave day → HRMS's handling wins; we add nothing and break nothing.
6. A public holiday → no absence marked. We must not have interfered.
7. A day with no check-ins at all → confirm HRMS marks Absent, and confirm the employee
   appears on our no-data list the same morning.
8. Under §8.5 option A (auto attendance off) → check-ins created, no `Attendance` written by
   anyone, manual marking still works.
9. Retry the same session write twice → exactly one pair of check-ins.
10. We never create, edit or delete an `Attendance` record ourselves. Assert it.

### 14.3 Integration tests — a fake watcher

Registration and approval, key delivery exactly once, key rotation, suspension mid-stream,
rate limiting, oversized bodies, sequence violations, clock drift, offline replay after a
4-hour outage, idempotent check-in creation under retry, and reports rejected while the
company is in manual mode.

### 14.4 Security tests — attempted and recorded, not reviewed

1. Call `report` with no key → rejected.
2. With a suspended key → rejected.
3. With another till's key → recorded against **that** till, not the one claimed. Prove the
   body cannot influence scope.
4. With a POS user's key → rejected.
5. With the watcher's key, try to read employees, customers, invoices, settings, sightings
   → every one rejected.
6. Another company's data with a valid key → rejected.
7. Replay a captured request → dropped.
8. Flood at 100 requests/second → rate limited, service healthy.
9. A cashier account against every pairing and settings endpoint → all rejected.
10. `request_join` from a POS session → creates `pending` only, never a key.
11. Confirm no presence job, endpoint or screen is active for a company in manual mode.
12. **Confirm `petromall` has presence off and appears in no scheduled job.**

### 14.5 Field tests — the ones that actually decide it

Software tests cannot prove this works. These can.

1. **Isolation test.** Run the scanner at the client's worst branch. Confirm devices are
   visible.
2. **Quiet-phone test.** One phone, one hour, screen locked in a pocket. **Measure how often
   it drops off and for how long.** This sets the departure wait. Everything else is
   guesswork until this is measured. Run it on an iPhone and an Android — they differ.
3. **Full day, one branch, shadow mode.** Presence recorded, no check-ins created. Compare
   against what actually happened. Count arrivals and departures wrong by more than 15
   minutes.
4. **Reboot test.** Restart a till mid-day. No false mass departure.
5. **Unplug test.** Internet off for 30 minutes. Nothing lost.
6. **Power-cut test.** Kill the only till at a branch. Branch shows unreachable, not empty.
7. **Busy-shop test.** Peak hour with customer phones. Device count under the cap, pairing
   screen still usable.
8. **Pairing test.** Pair five real staff. Time each. Over 30 seconds means the screen is
   wrong.
9. **Mode-off test.** Switch a live company back to manual. Confirm nothing is deleted and
   attendance still editable.

**Gate: no real check-ins for any client until test 3 has run for a full week at one branch
and the error rate is acceptable to the client.** Shadow mode first, always.

### 14.6 Accepted risk, not tested

- A phone deliberately left at the shop. Unfixable, stated to the client up front.
- A device id spoofed by someone technical on the shop wifi. The damage is a wrong
  attendance record that a manager can correct.

---

## 15. Build order

Every step ends with something that works. No step depends on the one after it.

| Step | What | Done when |
|---|---|---|
| **A** | Field-test the network at the worst branch | we know whether this is viable at all |
| **A2** | **Client-side HR setup**: Shift Types, shift assignments, holiday lists, and the §8.5 decision in writing | without this, nothing we build produces a single attendance record. It is their work, on their timeline, and it belongs in the quote. |
| **B** | Presence doctypes and settings inside `barakat`, mode default manual | migrates cleanly; a manual-mode company sees no change whatsoever |
| **C** | The pure engine + its unit tests | all of 14.2 passes with no database |
| **D** | Enrollment, keys, approval, the tills screen | 14.4 items 1–4 and 10 attempted and recorded |
| **E** | The report endpoint and the watcher, shipped in the POS installer | one branch reporting, shadow mode, no check-ins |
| **F** | Pairing screen in the Admin Panel | 14.5 test 8 passes |
| **G** | Merge, rot detection, alarms | 14.5 tests 3–7 pass |
| **H** | Turn on check-in creation for one company | after a full week of shadow mode, error rate agreed with the client |
| **I** | Retention and hashing | old sightings verified gone from the table, not merely scheduled |
| **J** | Break it on purpose, then the second client | 14.4 re-run against the live system |

**One client is a pilot. Two is a platform, and platforms leak sideways.** No second client
before step J.

---

## 16. Open decisions

1. **The departure wait is a guess until field test 14.5.2 is run.** 15 minutes is a
   placeholder. Do not create a real check-in based on a guessed number.
2. `rot_days` default of 7 is also a guess and should be reviewed after a month of real use.
3. Whether the stronger door in §10.6 is wanted for this client, or deferred indefinitely.
4. Whether the client wants every branch from day one, or one branch as a pilot.
5. Whether foot-traffic counting from hashed unknown devices becomes a product later. It is
   free from this data, but it is a separate conversation with the client about what we
   record.
6. **Whether POS evidence comes back in a later phase.** Removed by decision for now. If
   accuracy proves poor in shadow mode, this is the first thing to reconsider — it is the
   only cheap source of hard truth available.
7. **§8.5: which of options A, B or C the client chooses.** Blocking. No check-in creation
   is switched on for any company until this is answered in writing. Recommendation: **B**.
8. Whether `last_sync_of_checkin` is advanced by us or by the Shift Type's own auto-update
   setting. Either works; picking neither breaks everything silently (§2.3.1).
9. Whether the client is told about Frappe HR's existing location-based mobile check-in
   (§2.3.3). Recommendation: yes — a client who chooses the harder path should know they
   chose it.
