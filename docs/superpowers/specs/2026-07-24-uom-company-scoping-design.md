# UOM Company Scoping — Design (Piece 1)

**Date:** 2026-07-24
**Status:** Draft for review
**Related:** `2026-07-23-customer-company-scoping-design.md` (same pattern, immediate precedent)

## Problem

Units of Measure are **global** in this stack, by deliberate design. The proxy's
`fetchSelectableUoms` (products/service.ts) returns *every* enabled UOM to *every*
company, and `createUOM` writes them **unsuffixed** — with the documented rationale
"a kilogram is a kilogram", and the observation that filtering the picker to one
company's suffix would hide units a thousand items already use.

The business now requires **strict tenant isolation** for UOMs, identical to Items
and Customers: company A must never see or select company B's units, and must not
see the shared ERPNext defaults either — only its own.

### Why a name suffix is unavoidable

A UOM's ERPNext `name` **is** its `uom_name` and is the primary key. The `tabUOM`
name column collates `utf8mb4_unicode_ci` (case-insensitive). Verified on
bm.iztech.net: creating a second `Kg`, `KG`, or `kg` all fail with
`DuplicateEntryError` on PRIMARY. Therefore two companies cannot both own a unit
named `Kg`; the only way to give each its own is distinct names —
`Kg - <Company>`. The suffix is the enabling mechanism, not a stylistic choice.

The reason the global design was kept — "filtering the picker hides units items
use" — is dissolved by the **migration**: once every one of a company's items sits
on a scoped unit, filtering the picker to that company is safe.

## Goal

- Each company owns its own set of UOMs (`custom_company` + ` - <Company>` name).
- AP UOM picker and item forms show **only the active company's** units.
- No item, item-UOM row, or item price is left pointing at an unscoped (global)
  unit after migration.
- Runs cleanly across all barakat sites on test and prod. **`petromall` is not a
  barakat shop and must be skipped.**

## Non-goals (deferred to Piece 2 / later)

- Electrobun POS **receipt/cart display** of the unit (will show the raw
  `Kg - <Company>` until a later pass — explicitly accepted).
- The scale/electrical-balance **configuration feature** (new AP UOM tab,
  per-branch "balance UOM", moving scale-barcode config out of the app). Separate
  spec.

## Design

### 1. Data model — barakat app

Add one custom field, mirroring the other 17 scoped doctypes, to
`barakat/fixtures/custom_field.json`:

```
{ "dt": "UOM", "fieldname": "custom_company", "label": "Company (Barakat)",
  "fieldtype": "Link", "options": "Company", "insert_after": "enabled" }
```

Ownership mechanism = `custom_company` (like Item Group). Uniqueness carried by the
` - <Company name>` suffix on `uom_name`/`name`. The suffix token is the **company
name** (not abbr), consistent with Item / Item Group, so the proxy's
`stripCompanySuffix(name, activeCompany)` — where `activeCompany` is the Company
*name* — strips it for display.

### 2. Migration — barakat patch `scope_uom_company`

Registered in `barakat/patches.txt` as `barakat.patches.scope_uom_company`. Runs
per-site during `bench migrate`. Idempotent; a second run changes nothing.

**Guards**
- Skip the whole patch if `frappe.local.site == "petromall.iztech.net"` (and any
  other non-barakat site added to an explicit skip-set).
- Operate per **Company** on the site. A site with no Company does nothing.

**Built-in unit set** (never renamable — shared system-wide):
`{Unit, Kg, Nos, Set, Pair, Gram, Box, Kilogram}` (matched case-insensitively;
the concrete set is "any UOM that is NOT `custom_company`-owned and is referenced
by ≥1 of this company's items"). The distinction the patch actually uses:

> A unit is **rename-safe** for company C iff every Item / Item Price / item-UOM
> row that references it belongs to C AND it has no `custom_company` yet AND no
> *other* company references it. Otherwise it is **shared** → create+repoint.

**Algorithm (per company C):**
1. Collect `used = { stock_uom } ∪ { item UOM child .uom } ∪ { item price .uom }`
   over C's items/prices.
2. For each unit `u` in `used` already ending in ` - C`: skip (idempotent).
3. For each remaining `u`:
   - If **rename-safe**: `frappe.rename_doc("UOM", u, f"{u} - {C}")` (ERPNext
     cascades every Link: `stock_uom`, item-UOM child, `Item Price.uom`), then
     `frappe.db.set_value("UOM", f"{u} - {C}", "custom_company", C)`.
   - Else (**shared**): create `f"{u} - {C}"` (copy `enabled`,
     `must_be_whole_number`, `custom_company=C`) if missing, then explicitly
     re-point C's `Item.stock_uom`, `Item UOM` child rows, and `Item Price.uom`
     from `u` → `f"{u} - {C}"`. The global `u` is left intact.
4. Print counts: `renamed`, `created`, `items_repointed`, `prices_repointed`,
   `uom_rows_repointed`, `already_scoped`, and any `unmatched` (never blanked).

**Post-condition assertion (logged, not thrown):** zero of C's items/prices/rows
reference a unit lacking ` - C`.

### 3. Proxy (proxy-barakat)

- `src/lib/owned.ts`: add `UOM` to the `custom_company` mechanism so
  get/update/delete assert ownership.
- `products/service.ts`
  - `fetchSelectableUoms(erp, company)`: filter
    `[['custom_company','=',company],['enabled','=',1]]`; delete the stale
    "kilogram is a kilogram" comment and replace with the isolation rationale +
    a pointer to this spec.
  - `createUOM`: write suffixed `uom_name` via `makeScopedName(displayName, company)`
    and set `custom_company`. Return `displayName` stripped as today.
  - `resolveUomName` / `scoped-name.ts` UOM fallback: `'bare' → 'suffixed'`.
  - `stripCompanySuffix` display path unchanged (already correct).
- No change to how items store `stock_uom` (already the scoped value post-migration;
  `toItem` already strips for display).

### 4. Electrobun (compatibility only — no new UI)

- `src/bun/sync/pull-uoms.ts`: pulls UOMs where `must_be_whole_number=1`. Now the
  set holds scoped names (`Kg - Company`). Because `isWholeNumberUom` is
  **name-keyed**, and items now carry the scoped name, the lookup keeps working
  once the pull includes the company's scoped units. Confirm the pull is not
  company-filtered in a way that drops them; if it needs `custom_company`, add it.
- **New shared helper** `src/shared/uom.ts → isScaleKgUom(uom: string): boolean`:
  strip a trailing ` - <anything>` company suffix, `.trim().toLowerCase()`, return
  `["kg","kilogram"].includes(...)`. Replace the brittle equality checks in the
  scale/weighed mapping (`register-mappers.ts` and any `uom === "Kg"` site) so
  `Kg / KG / kg / Kilogram / "Kg - Beit Al-Moneh"` all count as the weighable unit.
  (Piece 2 later replaces this heuristic with the branch-configured balance UOM.)

## Test plan

### Unit
- **`isScaleKgUom` truth table:** `Kg,KG,kg,Kilogram,"Kg - Beit Al-Moneh","kilogram - X"` → true; `Bag,Unit,Piece,"Bagkg",""` → false.
- **`scoped-name` UOM:** `makeScopedName("Kg","Beit Al-Moneh") === "Kg - Beit Al-Moneh"`; idempotent on already-suffixed; `stripCompanySuffix` inverse.
- **`owned` UOM:** `isOwned('custom_company', {custom_company:'A'}, name, 'A')` true; `'B'` false; null company → true.

### Integration (proxy, against a seeded ERPNext or fake)
- `listUOMs(company=A)` returns only A's units, `displayName` stripped; excludes globals and B's.
- `createUOM("علبة", A)` → stored `"علبة - A"`, `custom_company=A`, returns `"علبة"`.
- `updateItem({uom:"Kg"}, company=A)` resolves to `stock_uom="Kg - A"`.
- Ownership: GET/PATCH a B-owned UOM as A → 404.

### Migration patch (against a copy of a real site)
- Idempotency: run twice → 2nd run `renamed=0 created=0 repointed=0`.
- Rename-safe unit: after run, the old bare name is gone, every referencing item now points at the scoped name (cascade verified).
- Shared unit (`Kg`): global `Kg` still exists; `Kg - C` created; **all** of C's items/prices/rows repointed; a *different* company's `Kg` items untouched.
- Post-condition: `SELECT COUNT(*)` of C's items on a non-`- C` unit == 0.
- `unmatched` values are reported, not blanked.
- **petromall:** patch is a no-op (asserted).

### Scenario (end-to-end, on a scratch site)
1. Import a shop's data (global units) → run patch → all items scoped.
2. AP: open item form → UOM picker lists only the shop's units.
3. Electrobun: open shift, scan a scale **weight** barcode for a `Kg - Company`
   walnut item → line adds fractional qty (`isScaleKgUom` true).
4. Push order → ERPNext POS Invoice line carries `uom = "Kg - Company"`, fractional
   qty, `update_stock`. Extends `push-orders-scale.spec.ts`.
5. Cross-company: a second company on the same site cannot see or pick company 1's
   `Kg - Company1`.

## Rollout

Order matters: **barakat app (adds field + patch) before the proxy filter**, so the
proxy never filters against a field/data that isn't there yet.

1. **barakat → test branch**, deploy, `bench migrate` **each test site**; read the
   printed counts; assert post-condition per site.
2. **proxy → test**, verify AP pickers on a test shop.
3. Repeat to **main/prod**: barakat → main, `bench migrate` each prod site **except
   petromall**; then proxy → main.
4. **Electrobun** ships on its own cadence (compatibility change is
   backward-safe: `isScaleKgUom` handles both suffixed and bare).

Per-site verification gate before moving on: `0` items on an unscoped unit, and the
AP UOM list shows only that company's units.

## Risks & mitigations

- **Half-done suffix scars** (a prior attempt left 1,054 items on `Kg - BOM`): the
  patch is data-driven off *current* references and idempotent, so it absorbs
  pre-existing suffixed rows (step 2 skip) rather than double-suffixing.
- **rename_doc cascade misses a link type** → the post-condition assertion catches
  any residual; shared-path repoint is explicit for the risky ones.
- **Electrobun pulls before app deploy** (units still bare): `isScaleKgUom` +
  name-keyed whole-number set both handle bare names, so an out-of-order deploy
  degrades gracefully, never breaks the till.

## Corrections applied during implementation

Two changes vs. the design above, found while building (see commits):

1. **Always copy+repoint; never rename.** The "rename-safe" path had a
   multi-company ordering hazard: after company C copies+repoints a shared unit
   (e.g. `Kg`), company D would see `Kg` as "only D references it" and rename the
   global — corrupting it system-wide. Every used unit now gets a scoped COPY and
   the global original is always left intact. Orphaned globals are invisible (the
   picker filters on `custom_company`), so this costs nothing.

2. **Item Price is scoped via its Item, not a `custom_company` column.** `Item
   Price` has no `custom_company` field; the patch joins `Item Price.item_code →
   Item.name` and filters `Item.custom_company`. Verified against real bm data
   (1,021 items + 1,694 prices repointed off bare `Kg` in a rolled-back dry-run).

3. **Electrobun has no literal kg-string check** to replace — the scan guard keys
   on `stockUomWholeNumber` (already naming-agnostic once scoped whole-number
   names are pulled). `isScaleKgUom` is added as the foundation Piece 2's
   per-branch balance UOM will consume.
