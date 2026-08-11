# The staff attendance system — the whole story

Written in plain words. No shortcuts, no jargon. If a technical word appears, it is
explained right next to it, once, and then never used again.

Date: 2026-08-11
Status: design, nothing built yet

---

## 1. What the client asked for

> "When my staff arrive in the morning and their phone joins the shop wifi, mark them
> present. When they leave and the phone disconnects, mark them gone."

That is the whole request. It is a good idea. It is also harder than it sounds, and this
document explains exactly how we'd do it, what it can and cannot promise, and in what
order we'd build it.

---

## 2. The honest truth about the idea, up front

Three things the client must hear before we start. If he hears them in month three
instead, we have an angry client.

**It knows roughly, not exactly.** We can say "he arrived around 8am and left around
5pm", give or take about 15 minutes. We cannot say "he clocked in at 7:58:12". If he
needs the second one, he needs a fingerprint machine and should buy one.

**It proves the phone is there, not the person.** Someone can leave his phone at the shop
and go home. There is no fix for this. Ever. It is a convenience tool, not a security
system.

**Some shops may need one setting changed on their router.** Cheap routers sometimes come
with a setting that stops devices on the wifi from seeing each other. Five minutes to fix,
but it is not always zero-touch, and we should not promise zero-touch.

---

## 3. The pieces, and where each one lives

Nothing here is magic. Six pieces. Two are brand new, four already exist.

| Piece | What it does | Where it lives | New? |
|---|---|---|---|
| **The watcher** | Sits on the till computer. Looks at the shop network. Reports what phones it sees. | Inside every shop, on the till PC | **NEW** |
| **The brain** | One program for all clients. Receives what every shop sees. Decides who is present. | AWS, next to the proxy | **NEW** |
| **The proxy** | Already the middle-man between the Admin Panel and ERPNext. Now also carries presence questions. | AWS | exists |
| **The Admin Panel** | Where the manager sets up staff and pairs phones. | AWS | exists |
| **The POS** | The till app. Already knows who logged in and when. Becomes a second source of truth. | Every till | exists |
| **ERPNext** | Where attendance is finally recorded, per client. | The live server | exists |

**The most important thing to understand about the layout:** the watcher is dumb and the
brain is smart. The watcher only *looks and reports*. Every decision happens in the brain.

That is deliberate. There will be dozens of watchers in the field that are hard to update.
There is one brain that we can fix in five minutes.

---

## 4. How the watcher actually sees a phone

The client's routers are cheap. So the design assumes **the router tells us nothing.**
We never ask it anything, we never log into it, we never need its password. That way the
brand of router stops mattering.

Instead, the watcher does this, from the till computer:

> Every 30 seconds it calls out to the whole shop network: *"anybody at address 1? address
> 2? address 3?"* — all 254 addresses at once. Phones answer automatically. They have no
> choice; it is the most basic thing a device on a network must do.

Like a teacher calling the register. She does not ask the building who is inside. She
stands in the room and calls names, and whoever is there answers.

**It works on any router, of any brand, at any price**, because we are talking to the
phones directly, not through the router.

**Proven, not guessed.** We ran this on a real network on 2026-08-11. It found 31 devices,
15 of them phones, and a full sweep took **0.9 to 1.35 seconds**. The number of staff makes
no difference — you are calling addresses, not people. Sixty staff takes exactly the same
time as five.

**The important detail from that test:** only 11 of the 31 devices actually replied to us.
The other 20 ignored us completely — and we found them anyway, because underneath there is
a lower-level question every device is forced to answer. If we had relied on the polite
reply we would have missed two thirds of the shop.

### What the watcher can and cannot tell apart

Every device on a network has an ID number. Modern phones **make up a fake ID** for
privacy, and that is actually useful to us: a made-up ID is almost always a phone, and a
real ID is almost always equipment — the router, the till, a printer, a camera.

So the watcher can say "I see 15 phones and 16 pieces of equipment" without knowing whose
phone any of them is. Connecting a phone to a person is a separate step (section 7).

---

## 5. A normal day, start to finish

Meet **Ahmad**, a cashier at the Ramallah branch.

**7:58** — Ahmad walks in. His phone joins the shop wifi on its own. He does nothing. No
app, no button, no fingerprint.

**7:58** — The watcher on the till calls the register. Ahmad's phone answers. The watcher
sends a note up: *"Ramallah: I can see these 18 devices right now."* It does not know who
Ahmad is and does not care.

**7:58** — The brain looks at the list. It recognises one of them as Ahmad's phone. It
writes down: **Ahmad, present at Ramallah, since 7:58.** Then it announces it out loud so
anyone listening can react.

**7:58** — A small program hears the announcement and tells ERPNext: *Ahmad, in, 7:58.*
Done. Ahmad touched nothing.

**11:30 — the tricky bit.** Ahmad puts his phone in his pocket. The screen sleeps and the
phone quietly drops off the wifi. **A naive system would now mark him as gone home.**

The brain does not believe it. It starts a 15-minute timer.

- Phone comes back at 11:33 → timer cancelled, nothing happened, Ahmad was never away.
- Phone still missing at 11:45 → now he is really gone.

Like a friend at a party stepping outside for a smoke. He did not leave the party. You only
say he left after he has been gone a while.

**5:12** — Ahmad actually goes home. Phone disappears. 15 minutes pass with nothing. The
brain records **left at 5:12** — the real time he vanished, not the time the timer finished.

**Day total: 7:58 to 5:12.** Automatic.

---

## 6. The safety net — and why it matters more than the wifi

Every shop has a till. Ahmad logs into it to open his shift. **That is a perfect, exact,
undeniable proof that he is in the building**, and it needs no wifi, no phone, no router,
and no new hardware. It exists today.

So we use both:

- **Wifi** gives the rough shape of the day — roughly in, roughly out.
- **Till login** gives the hard anchor — exactly when he started working.

If they disagree, the till wins.

**This is the single most important decision in the whole design.** It means:

- A shop with a hopeless network still gets attendance. Just less precise.
- A branch whose till PC was switched off still gets attendance from the other tills.
- If the whole wifi idea turns out to be a disaster, the client still has a product.

The system gets *worse* when things break. It does not *die*.

---

## 7. Setting up a person — connecting a phone to a name

A phone does not announce "I belong to Ahmad". It announces a number. Somebody has to
connect the two, once.

**Where:** the Admin Panel, under **Staff → Ahmad → Devices → Pair a device.**
The manager can do it from his own phone's browser while standing next to Ahmad.

**Why not on the till:** because pairing is exactly how you would cheat this system — pair
your friend's phone as yourself, leave it in the shop, go home. So it sits behind the
Manager role, where a cashier cannot reach it. And the Admin Panel is far easier to change
than the till app when we get the screen wrong the first two times.

**How it goes, and it takes 20 seconds:**

1. Ahmad stands at the till. The manager opens his page and clicks **Pair a device**.
2. The screen says: *"Ask Ahmad to turn his wifi off, then on again."*
3. Ahmad flips the switch he already knows how to flip. Nothing installed on his phone.
4. One device on the screen lights up — the one that just vanished and came back.
5. Manager taps it. Saved.

The toggle trick matters because a busy shop has 40 devices in range and there is no way
to guess which one is his. Making him flip the switch makes his phone raise its hand.

Every pairing is written into a log — who paired, which device, which person, when. If
someone cheats, it is visible afterwards.

---

## 8. When someone gets a new phone

This is not rare, and it is not only about buying a new phone.

It also happens when a staff member taps **"forget this network"** and rejoins, because
phones invent a new fake ID each time they join fresh. Same phone. Looks brand new to us.

So it cannot be a manual chore. It has to fix itself.

**How it fixes itself:** every time Ahmad logs into the till, we know for a fact he is in
the building. So the brain quietly checks — was his known phone there at that moment?

When he switches phones, that check starts failing. At the same time, a new unknown device
starts appearing **every single time Ahmad logs in, and never when he is off**.

The brain connects those two facts and puts a card on the manager's home screen:

> *"Ahmad's phone seems to have changed. This device has been with him for 4 shifts.
> Is this his?"* → **Yes / No**

One click. And while it is confused, Ahmad's hours still come from his till logins, so
attendance is never missing — only less precise.

**One rule underneath:** never delete an old pairing. Retire it with a date. If you delete
the phone Ahmad used in January, January's attendance becomes impossible to explain. And a
person can have more than one device — phone plus tablet. He is present if any of them is.

---

## 9. Many tills in one branch

A busy branch has three tills, so three watchers, all looking at the same room and
reporting the same phones.

**Not a problem — it is the best part of the design.**

The rule: **the branch is the truth, not the till.** The brain does not store "till 2 saw
Ahmad". It stores "Ramallah saw Ahmad".

- Present if **any** watcher sees him.
- Gone only when **no** watcher has seen him for the timer.

Three eyes in one room. If two tills are switched off, the third still covers the branch.
With one watcher, one switched-off PC means the whole branch records nobody at work all
day. This gives us backup for free.

**The one thing that will bite:** a till labelled with the wrong branch. Then Ahmad appears
in two places at once and his attendance becomes nonsense. So the branch name comes from
the till's existing POS setup, never typed in by hand during install. And if the same
person shows up at two branches in the same minute, that raises an alarm — it is physically
impossible, so it is always a setup mistake.

---

## 10. Security — the head office building

Now the part I explained badly the first time. One picture: **your head office building.**

### The building has two doors

**Door 1 — for the shops.** The guard opens it only if you show a badge that head office
itself printed. No badge, the door does not open. You do not get to knock, argue, or say
anything at all.

Most websites work the opposite way: you knock, the guard opens, asks your password, then
says no. **You still got to stand in the doorway and talk to him.** That is where people
find weak spots. Ours never opens. From the street it looks like a solid wall.

**Door 2 — inside the building.** For your own staff only: the proxy and the small program
that writes to ERPNext. There is no way to reach it from the street at all. It has no
address on the outside.

**There is no third door.**

### The badge

Every till gets **its own badge**. Never one badge that everyone copies — if a shared one
leaks, every shop of every client is open forever and there is no fix.

The badge says which client, which branch, which till. **The watcher never claims who it
is** — it is never asked. The badge already answered.

Badges expire after 90 days and renew themselves. A till we forget about closes its own
door. If one is stolen, one click cancels it.

### How a till gets its first badge, without leaving a gap

The obvious trap: you need a badge to come to the door, but you come to the door to get a
badge. The usual fix is a weaker "I'm new here" entrance — which is a permanent hole.

We do not build one. The till is **already logged in** — the POS has a real account today.
So the badge is handed over through the POS, using the login it already has. Door 1 is
badge-only from its very first minute. There is no mode where it is not.

### What a shop is allowed to do at Door 1

Only drop off a note: *"I can see these devices right now."*

It cannot ask for anything back. Not staff lists, not other branches, not other clients,
not history. Those things do not exist on that door.

### What head office is allowed to say back

Exactly one sentence: **"look faster for the next 2 minutes."**

That is the entire vocabulary. Never "run this", never "download that". The day a till can
be told to fetch and run something, we have installed a remote control on every till our
clients own, and we would not find out until it was used. Updates come through the POS
updater we already control.

### The clock

Shop computers have the wrong time constantly. **Head office writes the time on every
event. The shop's clock is never trusted** — it is stored only as a support hint, and if
it drifts far it becomes a health warning.

### If a badge is stolen anyway

Assume the worst and see what breaks. Someone with physical access to a till copies its
badge. They can send **fake sightings for that one till, at that one branch, of that one
client.** They cannot read anything, cannot see other branches, cannot touch ERPNext,
cannot touch money. One click cancels it.

And the same badge appearing from two places at once is an alarm — a till only exists in
one place.

### The data is sensitive, and we should act like it

This system holds a record of where named people were, all day, every day. That is
serious. Three rules from day one, because adding them later is miserable:

1. **The detailed second-by-second record is deleted after 30 days.** Only the daily
   in/out summary is kept long-term. Nobody needs to know where Ahmad stood at 2:47pm
   last March.
2. **Customer phones are scrambled, never stored as-is.** Hundreds of customers walk
   through these shops. A database of every customer's device is a liability with no
   upside.
3. **Nobody can ask for another client's data**, because nobody gets to say which client
   they want. Their badge already decided.

---

## 11. Everything that can go wrong

| What happens | What the system does |
|---|---|
| Phone sleeps in a pocket | Waits 15 minutes before believing it. No fake departure. |
| Shop internet goes down | Watcher keeps notes, sends them when it comes back. |
| Till PC switched off | Other tills cover the branch. If it is the only one, the branch shows **unreachable**, never **empty**. |
| Router blocks devices from seeing each other | Watcher sees zero devices including the router — impossible on a working network — so it reports itself blind. |
| Staff member gets a new phone | Detected automatically from till logins. One click to confirm. |
| Staff leaves the company | Pairings retired with a date. Old attendance still makes sense. |
| Till labelled with the wrong branch | Same person in two branches in one minute → alarm. |
| Someone copies a badge | Fake sightings for one till only. One click to cancel. |
| Stranger on the internet finds the door | Nothing. The door does not open. |
| The brain itself is broken into | It can lie about presence. It cannot write to ERPNext — a separate program does that with its own credentials and checks what it is told. |
| A cashier tries to pair a friend's phone | Cannot reach the screen. Manager role only. |
| A manager fakes someone's hours | Possible, but every change is logged with a name and a reason. Nothing is ever overwritten. |

---

## 12. The build plan, A to Z

Every step ends with something that works. No step needs the step after it.

**A. Prove the network first.** Take the scanner to the client's worst branch. Run it for
an hour. Find out whether we can see phones at all.
*Stop here if we cannot — and change the plan before spending money.*

**B. Build the brain, with no door to the outside.** The program and its database, reachable
only from inside. Nothing exposed.

**C. Build the badge system.** Printing badges, renewing them, cancelling them. Still no
outside door.

**D. Turn on the till-login source.** Attendance now works, end to end, into ERPNext.
**The client has a working product at the end of this step** — no phones, no wifi, no
outside door open.

**E. Open Door 1 for one pilot till.** Then prove it is shut for everyone else: try it with
no badge, a fake badge, an expired badge, another client's badge. Write down what each
attempt returned.
*If any attempt gets through to our code, this step is not finished.*

**F. Ship the watcher.** Inside the POS installer, as a background program that starts with
Windows. One branch, then one client, then the rest.

**G. Build the pairing screen** in the Admin Panel, manager-only, with the wifi-toggle
trick.

**H. Add the smart parts.** Merging several tills into one branch, the phone-change
detector, and all the alarms.

**I. Add the deleting.** The 30-day cleanup, the scrambling of customer devices.

**J. Try to break it on purpose** before the second client is added. Go through the table
in section 11 and actually attempt each one.
*One client is a pilot. Two clients is a platform, and platforms leak sideways.*

---

## 13. Still to decide

- Whether AWS's front desk can check badges the way we need in our region, or whether the
  brain has to check them itself. Needs confirming, not assuming.
- Where the master badge-printing key is kept, and who can reach it. It is the most
  valuable secret in the system — it creates identities.
- Whether the small program that writes to ERPNext runs next to the brain or on the
  ERPNext server. It holds ERPNext credentials, so it is a safety decision, not a
  convenience one.
- Whether the client wants this at every branch from day one, or one branch as a pilot.
