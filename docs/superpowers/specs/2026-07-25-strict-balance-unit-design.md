# Strict Balance Unit (no fallbacks) — Design

**Date:** 2026-07-25
**Status:** Approved direction (user: strict everywhere; warn-only on unit *change*)
**Supersedes:** the kg-spelling fallback in the POS scan guard.

## Problem

Today the POS falls back to "any kg-named unit is weighable" when no balance
unit is set, and the AP lets a branch enable scale barcodes with no unit
chosen. With mixed unit spellings (KG / kilo / Kilogram) this weighs the wrong
set of items, and configuring the unit later silently flips items on/off.
bm prod is already in the illegal state (branch enabled, no unit).

## Decision

**A branch may be enabled ONLY while the company has a balance unit.** The
till receives exactly two states: enabled-with-unit, or disabled. No fallback
matching, ever. Clearing the unit force-disables every branch of that company.
Changing the unit to a different one warns loudly but does not cascade.

## Enforcement — three layers

### 1. barakat app (authoritative — covers desk edits and any API path)

- `POSScaleSettings.validate`: if `scale_barcode_enabled` or `has_balances`
  is set and the row's company has an empty `custom_scale_uom` →
  `frappe.throw` (clear message naming the company).
- `doc_events` on **Company** `on_update` (hooks.py — hunk-stage around the
  concurrent WIP): if `custom_scale_uom` transitioned to empty → set
  `scale_barcode_enabled=0, has_balances=0` on every `POS Scale Settings`
  row with `custom_company == company` (scoped: other companies untouched).
- One-time patch `enforce_scale_unit_consistency` (registered in patches.txt,
  hunk-staged): for every company with an empty `custom_scale_uom`, zero the
  flags on its rows. Fixes bm's current illegal state. Idempotent; petromall
  irrelevant (no rows there) but guarded by the same SKIP_SITES habit.

### 2. proxy

`putScaleSettings` changes:
- `scaleUom: null` becomes a legal, explicit **clear**: writes
  `custom_scale_uom = ''` (the barakat hook then cascades the disable).
  `scaleUom` absent = don't touch (unchanged).
- **Reject 422** when the request would leave an enabled row without a unit:
  `barcode.enabled || hasBalances` is true AND the effective unit is empty
  (effective = value in this request if present, else the company's current).
  Setting the unit and enabling in the SAME request is allowed (unit written
  first).
- e2e additions: enable-without-unit → 422; set-unit+enable together → 200;
  clear+enable together → 422; clear alone → 200; partials unchanged.

### 3. AP

- Branch dialog: the "Scale barcode enabled" switch is **disabled with an
  explanatory hint** while the company unit is unset (dialog receives the
  current `scaleUom`).
- Balance-unit section:
  - Helper note: **"This should almost always be KG."** (en/ar/he)
  - A **Clear** action guarded by a confirm dialog that states exactly what
    will happen: "This switches scales OFF on N enabled branch(es)." On
    confirm → `PUT { scaleUom: null }` → invalidate scale-settings queries
    (the table refreshes showing everything Disabled).
  - On *changing* to a different unit (dirty ≠ server value ≠ empty): an
    inline warning near Save — "Items using the old unit will stop weighing;
    branches stay enabled." Warn-only, no cascade.

### 4. POS (electrobun)

- Scan guard becomes strict: weighable **iff** `scaleUom` is set AND
  `item.stockUom === scaleUom`. When `scaleUom` is null → always fallthrough
  (even if a stale sync says enabled — belt over the server guarantee).
- Delete `isScaleKgUom` (src/shared/uom.ts) and its uses/tests; rewrite the
  guard truth table (match / mismatch / null-unit all covered).

## Versions (semver per the barakat skill)

- barakat **1.2.0** (new enforcement hooks + patch)
- proxy **2.0.0** (PUT contract change: null-clear semantics + new rejections)
- AP **1.2.0** (gated switch, clear flow, warnings, KG note)
- POS **2.2.0** (behavior change: fallback removed)

## Edge cases (resolved)

| Case | Behavior |
|---|---|
| Existing enabled rows with no unit (bm today) | one-time patch disables them on migrate |
| Multi-company site clears one company's unit | cascade scoped by `custom_company` |
| Set unit + enable in one request | allowed (ordered writes) |
| Clear unit + enable in one request | 422 |
| Till syncs enabled=true but unit empty (drift) | guard treats as off |
| Balance UOM disabled later in ERPNext | unaffected at till (string match); AP picker simply can't re-choose it |
| Change unit Kg→X | warn-only; branches stay on; only exact-X items weigh |
| Desk edit bypassing AP | blocked/cascaded by the barakat hooks |

## Testing

- proxy e2e: the four new cases above + existing suite stays green.
- barakat: hook/patch logic verified live on qa-test during rollout (repo has
  no Frappe-runtime test harness; the pure-JSON tests don't apply here).
- POS: guard spec truth table rewritten; full scale spec files green.
- AP: typecheck + browser verification (no component-test infra).
- Prod smoke script unchanged (branch-only scratch still valid — it enables a
  branch only where a unit exists; bm will have Kg set during verification).

## Rollout

Same runbook as v1.1.0 (test EC2 → prod, per-site migrate, petromall
excluded), with the consistency patch watched on each site.
