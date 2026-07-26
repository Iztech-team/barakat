# Loyalty tier-name uniqueness — design

**Date:** 2026-07-26
**Repos touched:** `barakat` (Frappe app), `admin_panel_barakat` (AP), `electrobun-pos` (POS)
**Status:** approved, not yet implemented

## Problem

A Loyalty Program may hold two collection rules with the same `tier_name`. ERPNext
permits it; the POS cannot survive it.

The POS mirrors tiers into a local SQLite table whose primary key is
`(site_url, program, tier_name)` (`src/bun/db/schema.ts`, `loyaltyTiers`). The insert in
`upsertLoyaltyPrograms` (`src/bun/db/repos/loyalty-repo.ts`) has no conflict handling, so
the second row raises `UNIQUE constraint failed`. That insert runs inside one
`db.transaction`, so the failure rolls back **every** program, not just the offending one.
The scheduler classifies the error as non-retryable — it is not a network fault — so the
`loyalty_programs` scope goes red and stays red on every subsequent sync.

Observed on `qa-test.test.barakat.iztech.net`: the program `برنامج شرائح متعددة جديد`
carried two tiers named `vip vip` (min_spent 0 / factor 656567, and min_spent 2000 /
factor 50). All 18 programs failed to sync. Every HTTP request in that pull returned 200 —
the fetch was healthy and the failure was entirely local.

That row has since been deleted by hand. No duplicates remain anywhere (see
[Verified current state](#verified-current-state)).

## Goals

- A user cannot save a program with two tiers of the same name.
- Any duplicate that already exists, or that lands before the guard ships, is repaired
  without losing configuration.
- One badly-configured program can never again take down a till's whole loyalty sync.

## Non-goals

- Widening the rule to other fields. Duplicate `min_spent` is already handled by the AP
  and is out of scope here.
- Changing how tiers are selected or how points are earned.
- Any change to `petromall.iztech.net`. It shares the prod bench but is not a Barakat
  site; every site loop must exclude it and no command may target `--site all`.

## The shared rule

Defined once, applied in four places.

**Normalized key** — `tier_name`, trimmed of surrounding whitespace, case-folded. Two rows
in the same program sharing a normalized key are duplicates. Case folding is a no-op for
Arabic names and catches `VIP` / `vip` for Latin ones.

**Rename form** — keep the first row by `idx`; rename each later duplicate to
`<name> (2)`, `<name> (3)`, …, incrementing until the candidate matches no other tier name
in the same program (exact-string comparison, same as the rest of the repair paths).
Renaming rather than deleting preserves the row's `min_spent` and `collection_factor`, so
point earning is unchanged.

**Where each form applies:**

| Path | Match | Action |
| --- | --- | --- |
| Save-time checks (AP, Frappe hook) | normalized | reject |
| Repair paths (patch, POS pull) | exact string | rename |

The asymmetry is deliberate. Save-time is the moment to catch an invisible typo, so the
rule is friendly there. The repair paths run unattended against live configuration, so
they touch only the rows that genuinely break the POS — nothing is renamed on a program
that works.

## Layer 1 — AP (prevention)

`src/schemas/pages/customers/loyalty-program.ts`

- Add `duplicateTierName: string` to `LoyaltyProgramSchemaMessages`.
- In the existing `superRefine`, after the `duplicateMinSpent` loop, walk `vals.tiers`
  tracking normalized names in a `Set`; on a repeat, `ctx.addIssue` at path
  `['tiers', index, 'tierName']` with `msgs.duplicateTierName`.
- Add the placeholder `duplicateTierName: 'invalid'` to the default `loyaltyProgramSchema`.

Attaching the issue to the specific row — not to the `tiers` array root — is required for
React Hook Form to surface it when a single tier field changes. The `duplicateMinSpent`
rule already documents this; the new rule mirrors it exactly.

`src/hooks/pages/_app/customers/loyalty-manipulate.ts`

- Add `duplicateTierName: t('loyalty.validationDuplicateTierName')` to `useSchema()`.

`src/i18n/locales/{en,ar,he}.json`

- Add `loyalty.validationDuplicateTierName`, worded to match the neighbouring
  `validationDuplicateMinSpent` entries.

## Layer 2 — barakat app (enforcement)

`barakat/validations.py`

```
def validate_loyalty_program_tier_names(doc, method):
```

Walk `doc.collection_rules`, skipping rows with a blank `tier_name` — the child doctype
marks `tier_name` as `reqd`, so Frappe rejects blanks before this hook is reached, and the
skip only keeps the rule from inventing a second error for the same row. Track normalized
keys; on the first collision,
`frappe.throw` with a `title` and a `msg` naming the offending tier, following the shape of
`validate_customer_mobile_unique`.

`barakat/hooks.py`

Add to `doc_events`:

```
"Loyalty Program": {
    "validate": "barakat.validations.validate_loyalty_program_tier_names",
},
```

This is the layer that actually closes the hole. The proxy writes programs through
`erp.create` / `erp.update` on the `Loyalty Program` doctype, so the hook fires for the AP,
the ERPNext desk, and any direct API call — and a direct write is how the `vip vip` row
got in.

## Layer 3 — barakat app (repair)

`barakat/patches/dedupe_loyalty_tier_names.py`, registered in `patches.txt` under
`[post_model_sync]`.

- Return early unless `frappe.db.table_exists("Loyalty Program Collection")`, matching the
  defensive guard in `enforce_scale_unit_consistency`.
- Group rows by `parent`, ordered by `idx`. Within each parent, find exact-string repeats
  and rename later occurrences using the shared rename form.
- Write through `frappe.db.sql` / `frappe.db.set_value` on the child table so the parent's
  own validation is not re-run mid-migration, then `frappe.db.commit()`.
- `print` each rename as `<program>: <old> -> <new>` so the deploy output records exactly
  what changed on each site.

Idempotent by construction: after a run there are no exact repeats left, so a second run
renames nothing. It is a no-op on all nine sites today; it exists to catch duplicates
created between now and the deploy, and any site restored from an older backup.

The POS re-fetches every program on each pull — it lists all names and GETs each one,
with no `modified` checkpoint — so the patch does not need to touch the parent's timestamp
for tills to pick up the new names.

## Layer 4 — POS (resilience)

`src/bun/sync/pull-loyalty-programs.ts`

While building the `tiers` array for a program, track tier names already emitted for that
program and apply the shared rename form to any exact repeat before pushing the row. The
duplicate can then never reach the `loyalty_tiers` primary key.

Renaming rather than dropping matters: `loadLoyaltyContext` reads tiers back
`orderBy(asc(loyaltyTiers.minSpent))` to pick a customer's collection factor, so discarding
a row would silently change what customers earn. The name does not drive tier selection,
so a suffixed name is behaviour-neutral. It also produces exactly the names Layer 3 would
produce, so a till that syncs before the patch runs and one that syncs after agree.

No local schema change and no drizzle migration: the table's primary key is untouched.

## Error handling

The Frappe `throw` is the only hard failure, and it surfaces on a save the user is
watching. The patch and the POS never throw — they repair and continue. Existing programs
are unaffected: no site holds a normalized-rule collision, so the new save-time check
cannot make a currently-valid program un-saveable.

## Testing

**barakat** — `barakat/test_validations.py` (exists):

- a program with distinct tier names saves;
- two tiers with an identical name are rejected;
- two tiers differing only by case or surrounding whitespace are rejected;
- a blank tier name does not trip this rule.

Plus a patch test: a program seeded with an exact duplicate is renamed to `<name> (2)` with
`min_spent` and `collection_factor` preserved, and a second run makes no further change.

**POS** — `src/bun/sync/pull-loyalty-programs.spec.ts` (exists): a program returning two
same-named collection rules upserts without throwing, and both rows land with distinct
names and their original thresholds. Run via `bun run verify`.

**AP** — the repo has no test runner. Covered by `verify` (typecheck + lint) and a manual
pass on the loyalty program form: adding a second tier with an existing name shows the red
line on that row and blocks Save.

## Rollout

Work lands on `dev` in each repo first, then promotes `dev` → `test` → `main`.

| Component | Version | Deploy |
| --- | --- | --- |
| barakat | minor — new validation rule and patch (`barakat/__init__.py`) | push, then on the test box (`test` branch) and the prod box (`main` branch): `git pull`, `bench --site <site> migrate`, `bench restart` |
| AP | minor — new save-time rule the user sees (`package.json`) | deploy-on-push; bump before promoting |
| POS | patch — crash fix, no new behaviour (`electrobun.config.ts`) | `build:test` + `release:test`, then `build:prod` + `release:prod`; tag `main` as `v<version>` |

The proxy is not touched.

Migration runs per named site. Enumerate sites with the petromall filter and never use
`--site all`:

```
sudo -u frappe ls sites | grep -v -E 'assets|apps|common|\.json|\.log|\.txt' | grep -v petromall
```

The patch's `print` output is the record of what it changed; expect it to be silent
everywhere given the state below.

## Verified current state

Scanned 2026-07-26, petromall excluded throughout. No exact duplicates and no
normalized-rule collisions on any site.

| Box | Sites | Tier rows | Duplicates |
| --- | --- | --- | --- |
| test | fatima, master, qa-test, qa-training | 0 / 0 / 25 / 14 | 0 |
| prod | barakat, bm, bom, niveen1, test | 0 / 1 / 5 / 4 / 0 | 0 |
