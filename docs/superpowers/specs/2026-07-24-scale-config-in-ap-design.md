# Scale Configuration in the AP (Piece 2) — Design

**Date:** 2026-07-24
**Status:** Draft for review
**Depends on:** `2026-07-24-uom-company-scoping-design.md` (Piece 1 — `custom_scale_uom` points at a scoped UOM; `isScaleKgUom` is the fallback matcher)

## Problem

The weighing-scale (electrical-balance) config lives **device-local** in the
electrobun app: `scale-barcode-store.ts` writes a JSON file per till, editable by
any cashier from Settings, and it never touches ERPNext. That means:

- Every till is configured by hand; a new device starts blank.
- Any cashier can change the barcode parsing; there is no role control.
- There is no server record of which branch weighs, in what unit, with what
  barcode format.

The business wants this config **managed centrally in the AP**, per branch, by
Managers (and item-write roles), and **synced down** to the tills — where it
becomes read-only, shown in Settings → Device info.

## Goal

- A Manager (or item-write persona) configures, per branch: whether the branch
  has balances, and the scale-barcode format. The **balance UOM** (which unit a
  weighed quantity is in — usually the company `Kg`) is set once, **company-wide**.
- The AP surfaces this in a new **Units** tab (which also lists the company's
  UOMs from Piece 1).
- The electrobun till **pulls** the config on Sync and renders it **read-only** in
  Device info; cashiers can no longer edit it on the device.
- Weighed-item detection uses the configured balance UOM, superseding the
  `must_be_whole_number` heuristic.

## Non-goals

- Auto-migrating existing device-local `scale-barcode-settings.json` into ERPNext
  (admins re-enter each branch in the AP — YAGNI).
- Per-branch balance UOM (explicitly company-wide per the product decision).
- Changing the scale barcode *parsing* logic (`parseScaleBarcode`,
  `computeScaleLine`) — only where its settings come from.

## Design

### 1. Data model — barakat app

- **Company** custom field `custom_scale_uom` (Link → UOM), in
  `barakat/fixtures/custom_field.json`. The company-wide balance unit. Its options
  are the company's own scoped UOMs (Piece 1).
- **New DocType `POS Scale Settings`** (shipped as a fixture / in the app's
  `doctype/` dir), **one per branch**:
  - `branch` (Link → Branch) — the key. `autoname = field:branch`.
  - `custom_company` (Link → Company) — scoping marker (mirrors the other
    doctypes; set on write, filtered on read).
  - `has_balances` (Check, default 0).
  - `scale_barcode_enabled` (Check, default 0).
  - `scale_barcode_prefix` (Data, default "2").
  - `scale_barcode_code_length` (Int, default 7).
  - `scale_barcode_value_type` (Select: `price`\n`weight`, default `price`).
  - `scale_barcode_value_length` (Int, default 5).
  - `scale_barcode_decimals` (Int, default 2).

  Field shape mirrors `electrobun/src/shared/scale-barcode.ts::ScaleBarcodeSettings`
  so the synced payload maps 1:1.

### 2. Proxy — `scale-settings` module

New module `src/modules/scale-settings/` (service + index + types), mounted under
the AP API.

- `GET /api/scale-settings?branch=<branch>` → `{ companyScaleUom, branch,
  hasBalances, barcode: { enabled, prefix, codeLength, valueType, valueLength,
  decimals } }`. Reads `Company.custom_scale_uom` + the branch's `POS Scale
  Settings` (defaults if the row doesn't exist yet). `companyScaleUom` display-
  stripped for the picker.
- `GET /api/scale-settings/branches` (or reuse the pos-profiles branch list) →
  the company's branches for the selector.
- `PUT /api/scale-settings` `{ scaleUom, branch, hasBalances, barcode {...} }` →
  set `Company.custom_scale_uom = makeScopedName(scaleUom, company)` (resolve to
  the real UOM) and upsert the branch's `POS Scale Settings` (create if missing,
  `custom_company = company`). Validates the branch `endsWith(" - " + company)`
  and the UOM is one of the company's.

**Authorization:** the write is gated in `src/middleware/permission.ts` on the
same key as item mutation (`products`, action `mutate`) — granted to **Manager,
Branch Supervisor, Inventory Keeper** (the `products:'write'` catalog personas);
Cashier / Accountant / HR get 403. The GET is readable by any authenticated
caller (the till reads it too, but see below it reads ERPNext directly).

### 3. AP — new "Units" tab

A new sidebar entry **الوحدات / Units** (under Settings or Products — matching the
existing IA), with three parts:

1. **UOM list** — the company's units via Piece 1's `listUOMs` (view; create is
   the existing `createUOM`).
2. **Balance UOM** — a company-level picker bound to `custom_scale_uom` (options =
   the company's UOMs). "Which unit do your scales weigh in? (usually Kg)".
3. **Per-branch scale panel** — a branch selector; for the chosen branch:
   `has_balances` toggle, then (when on) the barcode-format form. Save calls
   `PUT /api/scale-settings`.

Edit controls render only for Manager + item-write personas (the AP already gates
by the persona matrix); others see it read-only.

### 4. Electrobun — config flows AP → device (read-only)

- **Remove** `settings-scale-barcode-dialog.tsx` (the editable local dialog) and
  the `setScaleBarcodeSettings` RPC/write path. `getScaleBarcodeSettings` stays
  (now returns the synced cache).
- **New pull** `src/bun/sync/pull-scale-settings.ts`: on Sync, resolve the
  device's branch (from its POS profile → branch), then fetch `Company.custom_scale_uom`
  + the branch's `POS Scale Settings` directly from ERPNext (same `erpnextRequest`
  idiom as `fetchSiteSettings`), normalise into `ScaleBarcodeSettings` +
  `scaleUom`, and persist via `scale-barcode-store` (now a synced cache, not a
  user-editable store). Registered in the sync scheduler.
- `register-page.tsx` reads the cached settings for `parseScaleBarcode` (shape
  unchanged).
- **`scale-scan-guard.ts`**: an item is weighable iff its `stock_uom` equals the
  synced `scaleUom` (compared via `isScaleKgUom` for kg-spelling tolerance, or
  exact match to the configured unit) — replacing the `stockUomWholeNumber` gate.
- **Settings → Device info** (`settings-device-info-card.tsx`): render the synced
  scale settings **read-only** (has balances, balance UOM, barcode format).

### 5. Transition

Existing local `scale-barcode-settings.json` is overwritten by the first post-
deploy Sync (which now writes the synced value). Until a branch is configured in
the AP, the till gets the defaults (`enabled=false`) — i.e. scale scanning is off
until an admin turns it on centrally. No auto-migration.

## Test plan

### barakat
- `Company-custom_scale_uom` is a Link Custom Field → UOM in `custom_field.json`
  (extend `test_custom_fields.py`, same guard as the other markers).
- `POS Scale Settings` doctype ships as a fixture with all fields, and its
  `custom_company` is a **native Link field of the doctype** (not a Custom Field).
  A fixture-presence test asserts the doctype JSON exists with the expected
  fieldnames.

### proxy (unit + integration, fake erp)
- `GET` returns company `scaleUom` (stripped) + branch defaults when no row.
- `PUT` sets `custom_scale_uom` (resolved to the scoped UOM) and upserts the branch
  row with `custom_company`.
- **Role-gating:** PUT as Manager/Branch Supervisor/Inventory Keeper → allowed; as
  Cashier/Accountant/HR → 403.
- Scoping: PUT with a branch not `- <company>` → 422; a UOM not the company's → 422.

### electrobun (bun test)
- `pull-scale-settings`: given a fake ERPNext returning a branch row + company
  `scale_uom`, stores the normalised `ScaleBarcodeSettings` + `scaleUom`; a missing
  row yields defaults (`enabled=false`).
- `scale-scan-guard`: item whose `stock_uom` matches the balance UOM →
  `{action:"scale"}`; a non-matching unit → `fallthrough`. Truth table across
  `Kg / Kg - Company / Piece`.
- Device-info card renders the synced values read-only (no setter wired).

### Scenario (end-to-end)
1. Manager opens Units tab → sets company balance UOM = `Kg`, branch "Test Branch"
   → has_balances on, barcode `prefix=2, weight, …`. Saves (PUT).
2. Till on Test Branch runs Sync → `pull-scale-settings` caches it.
3. Settings → Device info shows: balances on, Kg, the barcode format (read-only).
4. Cashier scans a weight barcode for a `Kg - Company` item → scan guard returns
   `scale`, the weighed line is added in Kg.
5. A Cashier persona attempting the AP PUT is 403.

## Rollout

barakat (doctype + field) → migrate each test site → proxy → AP verify → electrobun
build (test channel) → repeat to prod (skip petromall). The electrobun change is
backward-safe: until the pull ships, tills keep their local store; after it ships,
the first Sync takes over.
