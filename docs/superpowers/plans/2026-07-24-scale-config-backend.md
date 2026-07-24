# Scale Config — Backend + Till (Piece 2A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store scale/electrical-balance config server-side (company-wide balance UOM on Company + a per-branch `POS Scale Settings` doctype), expose role-gated read/write through the proxy, and have the electrobun till pull it read-only on Sync (guard + device-info), replacing the device-local store as the source of truth.

**Architecture:** barakat adds `Company.custom_scale_uom` + a `POS Scale Settings` doctype. The proxy `scale-settings` module reads/writes them (write gated to item-write personas). The till's Sync fetches them straight from ERPNext (like `fetchSiteSettings`) into the existing `scale-barcode-store` cache; the scan guard keys on the balance UOM; Device info renders them read-only. The AP UI (Plan 2B) consumes the proxy endpoints.

**Tech Stack:** Frappe/ERPNext (Python) for barakat; Bun + Elysia + TypeScript for proxy; Bun + TypeScript for electrobun. Tests: `python -m unittest`, `bun test`.

## Global Constraints

- Balance UOM is **company-wide** (`Company.custom_scale_uom`); `has_balances` + barcode format are **per-branch** (`POS Scale Settings`, autoname = `field:branch`).
- Barcode field shape mirrors `electrobun/src/shared/scale-barcode.ts::ScaleBarcodeSettings` exactly: `enabled, prefix, codeLength, valueType('price'|'weight'), valueLength, decimals`.
- Proxy write gated to **Manager, Branch Supervisor, Inventory Keeper** (the `products:'write'` catalog personas); Cashier/Accountant/HR → 403.
- Branch must `endsWith(" - " + company)`; `scaleUom` must be one of the company's UOMs.
- The till reads settings **directly from ERPNext** on Sync (not via the proxy), same idiom as `pull-site-settings.ts`.

---

### Task 1: barakat — `Company.custom_scale_uom` field

**Files:**
- Modify: `barakat/fixtures/custom_field.json`
- Test: `barakat/test_custom_fields.py`

**Interfaces:**
- Produces: Custom Field `Company-custom_scale_uom` (Link → UOM).

- [ ] **Step 1: Write the failing test** — add to `test_custom_fields.py`:

```python
    def test_company_scale_uom_links_to_uom(self):
        f = _by_name(self.rows, "Company-custom_scale_uom")
        self.assertIsNotNone(f, "Company-custom_scale_uom missing from fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "UOM")
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd ~/Desktop/bar/barakat && python -m unittest barakat.test_custom_fields -v`
Expected: FAIL — `Company-custom_scale_uom missing from fixtures`

- [ ] **Step 3: Append the fixture entry** (before the closing `]`):

```json
{
 "doctype": "Custom Field",
 "name": "Company-custom_scale_uom",
 "dt": "Company",
 "fieldname": "custom_scale_uom",
 "label": "Scale / Balance UOM (Barakat)",
 "fieldtype": "Link",
 "options": "UOM",
 "insert_after": "default_finance_book"
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `python -c "import json;json.load(open('barakat/fixtures/custom_field.json'))" && python -m unittest barakat.test_custom_fields -v`
Expected: JSON valid + PASS

- [ ] **Step 5: Commit**

```bash
git add barakat/fixtures/custom_field.json barakat/test_custom_fields.py
git commit -m "feat(scale): add Company.custom_scale_uom balance-UOM field"
```

---

### Task 2: barakat — `POS Scale Settings` DocType

**Files:**
- Create: `barakat/barakat/doctype/pos_scale_settings/__init__.py` (empty)
- Create: `barakat/barakat/doctype/pos_scale_settings/pos_scale_settings.json`
- Create: `barakat/barakat/doctype/pos_scale_settings/pos_scale_settings.py`
- Create: `barakat/test_pos_scale_settings_doctype.py`

**Interfaces:**
- Produces: DocType `POS Scale Settings` — one row per branch, fields `branch`, `custom_company`, `has_balances`, `scale_barcode_enabled`, `scale_barcode_prefix`, `scale_barcode_code_length`, `scale_barcode_value_type`, `scale_barcode_value_length`, `scale_barcode_decimals`.

- [ ] **Step 1: Write the failing test** — `barakat/test_pos_scale_settings_doctype.py`:

```python
import json, pathlib, unittest

DOCTYPE = pathlib.Path(__file__).resolve().parent / "barakat" / "doctype" / \
    "pos_scale_settings" / "pos_scale_settings.json"

class PosScaleSettingsDoctype(unittest.TestCase):
    def setUp(self):
        self.dt = json.loads(DOCTYPE.read_text(encoding="utf-8"))
        self.fields = {f["fieldname"]: f for f in self.dt["fields"]}

    def test_autoname_by_branch(self):
        self.assertEqual(self.dt.get("autoname"), "field:branch")

    def test_branch_and_company_links(self):
        self.assertEqual(self.fields["branch"]["options"], "Branch")
        self.assertEqual(self.fields["custom_company"]["options"], "Company")

    def test_barcode_fields_present(self):
        for fn in ["has_balances", "scale_barcode_enabled", "scale_barcode_prefix",
                   "scale_barcode_code_length", "scale_barcode_value_type",
                   "scale_barcode_value_length", "scale_barcode_decimals"]:
            self.assertIn(fn, self.fields, fn)

    def test_value_type_options(self):
        self.assertEqual(self.fields["scale_barcode_value_type"]["options"], "price\nweight")
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m unittest barakat.test_pos_scale_settings_doctype -v`
Expected: FAIL — file not found

- [ ] **Step 3: Create the doctype JSON** — `pos_scale_settings.json`:

```json
{
 "actions": [],
 "autoname": "field:branch",
 "creation": "2026-07-24 00:00:00",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": ["branch","custom_company","has_balances","scale_barcode_enabled",
   "scale_barcode_prefix","scale_barcode_code_length","scale_barcode_value_type",
   "scale_barcode_value_length","scale_barcode_decimals"],
 "fields": [
  {"fieldname":"branch","fieldtype":"Link","label":"Branch","options":"Branch","reqd":1,"unique":1},
  {"fieldname":"custom_company","fieldtype":"Link","label":"Company (Barakat)","options":"Company"},
  {"fieldname":"has_balances","fieldtype":"Check","label":"Has Electrical Balances","default":"0"},
  {"fieldname":"scale_barcode_enabled","fieldtype":"Check","label":"Scale Barcode Enabled","default":"0"},
  {"fieldname":"scale_barcode_prefix","fieldtype":"Data","label":"Barcode Prefix","default":"2"},
  {"fieldname":"scale_barcode_code_length","fieldtype":"Int","label":"Code Length","default":"7"},
  {"fieldname":"scale_barcode_value_type","fieldtype":"Select","label":"Value Type","options":"price\nweight","default":"price"},
  {"fieldname":"scale_barcode_value_length","fieldtype":"Int","label":"Value Length","default":"5"},
  {"fieldname":"scale_barcode_decimals","fieldtype":"Int","label":"Decimals","default":"2"}
 ],
 "links": [],
 "modified": "2026-07-24 00:00:00",
 "module": "Barakat",
 "name": "POS Scale Settings",
 "owner": "Administrator",
 "permissions": [
  {"role":"System Manager","read":1,"write":1,"create":1,"delete":1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC"
}
```

- [ ] **Step 4: Create the controller + package init**

`pos_scale_settings/__init__.py`: empty file.
`pos_scale_settings/pos_scale_settings.py`:

```python
from frappe.model.document import Document


class POSScaleSettings(Document):
    pass
```

- [ ] **Step 5: Run — expect PASS**

Run: `python -m unittest barakat.test_pos_scale_settings_doctype -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add barakat/barakat/doctype/pos_scale_settings barakat/test_pos_scale_settings_doctype.py
git commit -m "feat(scale): POS Scale Settings per-branch doctype"
```

---

### Task 3: proxy — `scale-settings` service

**Files:**
- Create: `src/modules/scale-settings/types.ts`
- Create: `src/modules/scale-settings/service.ts`
- Test: `src/modules/scale-settings/service.spec.ts`

**Interfaces:**
- Consumes: `Company.custom_scale_uom`, `POS Scale Settings` (Task 1–2); `stripCompanySuffix`/`makeScopedName` idioms.
- Produces:
  - `getScaleSettings(erp, company, branch) → { scaleUom: string|null, branch, hasBalances, barcode: Barcode }`
  - `putScaleSettings(erp, company, body) → same shape` where `body = { scaleUom, branch, hasBalances, barcode }`.
  - `type Barcode = { enabled:boolean; prefix:string; codeLength:number; valueType:'price'|'weight'; valueLength:number; decimals:number }`

- [ ] **Step 1: Write the failing test** — `service.spec.ts` (fake erp capturing reads/writes):

```ts
import { describe, test, expect } from 'bun:test'
import { getScaleSettings, putScaleSettings } from './service'

function fakeErp(company: { custom_scale_uom?: string }, row: Record<string, unknown> | null) {
  const calls: { updatedCompany?: unknown; upserted?: unknown } = {}
  return {
    calls,
    get: async (dt: string) => ({ data: dt === 'Company' ? company : {} }),
    list: async (dt: string) => ({ data: dt === 'POS Scale Settings' && row ? [row] : [] }),
    update: async (dt: string, _n: string, doc: unknown) => { if (dt === 'Company') calls.updatedCompany = doc; else calls.upserted = doc; return { data: {} } },
    create: async (_dt: string, doc: unknown) => { calls.upserted = doc; return { data: { name: 'x' } } },
  } as never
}

describe('scale-settings service', () => {
  test('getScaleSettings returns company uom (stripped) + branch defaults when no row', async () => {
    const erp = fakeErp({ custom_scale_uom: 'Kg - Beit Al-Moneh' }, null)
    const res = await getScaleSettings(erp, 'Beit Al-Moneh', 'Test Branch - Beit Al-Moneh')
    expect(res.scaleUom).toBe('Kg')
    expect(res.hasBalances).toBe(false)
    expect(res.barcode.enabled).toBe(false)
    expect(res.barcode.prefix).toBe('2')
  })

  test('putScaleSettings sets company uom (scoped) + upserts branch row', async () => {
    const erp = fakeErp({}, null)
    await putScaleSettings(erp as never, 'Beit Al-Moneh', {
      scaleUom: 'Kg', branch: 'Test Branch - Beit Al-Moneh', hasBalances: true,
      barcode: { enabled: true, prefix: '2', codeLength: 7, valueType: 'weight', valueLength: 5, decimals: 2 },
    })
    expect((erp as never as { calls: { updatedCompany: { custom_scale_uom: string } } }).calls.updatedCompany.custom_scale_uom).toBe('Kg - Beit Al-Moneh')
    const up = (erp as never as { calls: { upserted: Record<string, unknown> } }).calls.upserted
    expect(up.custom_company).toBe('Beit Al-Moneh')
    expect(up.has_balances).toBe(1)
    expect(up.scale_barcode_value_type).toBe('weight')
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd ~/Desktop/barakat-repos/proxy-barakat && bun test src/modules/scale-settings/service.spec.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `types.ts`**

```ts
import { t } from 'elysia'

export const ScaleBarcodeSchema = t.Object({
  enabled: t.Boolean(),
  prefix: t.String(),
  codeLength: t.Number(),
  valueType: t.Union([t.Literal('price'), t.Literal('weight')]),
  valueLength: t.Number(),
  decimals: t.Number(),
})

export const PutScaleSettingsBody = t.Object({
  scaleUom: t.String(),
  branch: t.String({ minLength: 1 }),
  hasBalances: t.Boolean(),
  barcode: ScaleBarcodeSchema,
})

export type Barcode = {
  enabled: boolean; prefix: string; codeLength: number
  valueType: 'price' | 'weight'; valueLength: number; decimals: number
}
```

- [ ] **Step 4: Implement `service.ts`**

```ts
import type { ERPNextClient } from '../../lib/erpnext'
import { AppError } from '../../lib/errors'
import { makeScopedName, resolveScopedName } from '../../lib/scoped-name'
import type { Barcode } from './types'

const DEFAULT_BARCODE: Barcode = {
  enabled: false, prefix: '2', codeLength: 7, valueType: 'price', valueLength: 5, decimals: 2,
}

const strip = (name: string | null | undefined, company: string) =>
  name && name.endsWith(` - ${company}`) ? name.slice(0, -(3 + company.length)) : (name ?? null)

interface ERPRow {
  name: string; has_balances?: 0 | 1
  scale_barcode_enabled?: 0 | 1; scale_barcode_prefix?: string
  scale_barcode_code_length?: number; scale_barcode_value_type?: 'price' | 'weight'
  scale_barcode_value_length?: number; scale_barcode_decimals?: number
}

async function fetchRow(erp: ERPNextClient, company: string, branch: string): Promise<ERPRow | null> {
  const res = await erp.list<ERPRow>('POS Scale Settings', {
    fields: JSON.stringify(['name', 'has_balances', 'scale_barcode_enabled', 'scale_barcode_prefix',
      'scale_barcode_code_length', 'scale_barcode_value_type', 'scale_barcode_value_length', 'scale_barcode_decimals']),
    filters: JSON.stringify([['branch', '=', branch], ['custom_company', '=', company]]),
    limit_page_length: 1,
  }).catch(() => ({ data: [] as ERPRow[] }))
  return res.data[0] ?? null
}

function toBarcode(r: ERPRow | null): Barcode {
  if (!r) return { ...DEFAULT_BARCODE }
  return {
    enabled: r.scale_barcode_enabled === 1,
    prefix: r.scale_barcode_prefix ?? DEFAULT_BARCODE.prefix,
    codeLength: r.scale_barcode_code_length ?? DEFAULT_BARCODE.codeLength,
    valueType: r.scale_barcode_value_type ?? DEFAULT_BARCODE.valueType,
    valueLength: r.scale_barcode_value_length ?? DEFAULT_BARCODE.valueLength,
    decimals: r.scale_barcode_decimals ?? DEFAULT_BARCODE.decimals,
  }
}

export async function getScaleSettings(erp: ERPNextClient, company: string, branch: string) {
  const [companyRes, row] = await Promise.all([
    erp.get<{ custom_scale_uom?: string | null }>('Company', company),
    fetchRow(erp, company, branch),
  ])
  return {
    scaleUom: strip(companyRes.data.custom_scale_uom, company),
    branch,
    hasBalances: row?.has_balances === 1,
    barcode: toBarcode(row),
  }
}

export async function putScaleSettings(erp: ERPNextClient, company: string, body: {
  scaleUom: string; branch: string; hasBalances: boolean; barcode: Barcode
}) {
  if (!body.branch.endsWith(` - ${company}`)) {
    throw new AppError(422, `Branch "${body.branch}" is not in this company.`)
  }
  const uom = await resolveScopedName(erp, 'UOM', body.scaleUom, company, 'suffixed')
  await erp.update('Company', company, { custom_scale_uom: uom })

  const fields = {
    has_balances: body.hasBalances ? 1 : 0,
    scale_barcode_enabled: body.barcode.enabled ? 1 : 0,
    scale_barcode_prefix: body.barcode.prefix,
    scale_barcode_code_length: body.barcode.codeLength,
    scale_barcode_value_type: body.barcode.valueType,
    scale_barcode_value_length: body.barcode.valueLength,
    scale_barcode_decimals: body.barcode.decimals,
  }
  const existing = await fetchRow(erp, company, body.branch)
  if (existing) await erp.update('POS Scale Settings', existing.name, fields)
  else await erp.create('POS Scale Settings', { branch: body.branch, custom_company: company, ...fields })

  return getScaleSettings(erp, company, body.branch)
}
```

Note: `resolveScopedName` (unused var `makeScopedName`) — drop the unused import; keep `resolveScopedName`.

- [ ] **Step 5: Run — expect PASS + typecheck**

Run: `bun test src/modules/scale-settings/service.spec.ts && bun run typecheck`
Expected: PASS + clean

- [ ] **Step 6: Commit**

```bash
git add src/modules/scale-settings/types.ts src/modules/scale-settings/service.ts src/modules/scale-settings/service.spec.ts
git commit -m "feat(scale): proxy scale-settings service (company uom + per-branch row)"
```

---

### Task 4: proxy — `scale-settings` routes + role gate

**Files:**
- Create: `src/modules/scale-settings/index.ts`
- Modify: `src/app.ts` (mount the module) — follow how other modules are `.use()`d
- Modify: `src/middleware/permission.ts` (gate PUT on `products:mutate`) — follow the existing route→permission map
- Test: `src/modules/scale-settings/route.spec.ts` (if the repo tests routes; else assert the permission-map entry)

**Interfaces:**
- Consumes: `getScaleSettings`/`putScaleSettings`, `PutScaleSettingsBody` (Task 3), `siteGuard`, the active-company resolution used by other modules.
- Produces: `GET /api/scale-settings?branch=…`, `PUT /api/scale-settings`.

- [ ] **Step 1: Implement `index.ts`** (mirror `pos-profiles/index.ts` — `siteGuard`, company from context):

```ts
import { Elysia, t } from 'elysia'
import { siteGuard } from '../../middleware/site'
import { PutScaleSettingsBody } from './types'
import * as service from './service'

export const scaleSettings = new Elysia({ prefix: '/api/scale-settings' })
  .use(siteGuard)
  .get('/', ({ erp, company, query }) =>
    service.getScaleSettings(erp, company as string, query.branch), {
    query: t.Object({ branch: t.String({ minLength: 1 }) }),
  })
  .put('/', ({ erp, company, body }) =>
    service.putScaleSettings(erp, company as string, body), {
    body: PutScaleSettingsBody,
  })
```

Adjust `erp`/`company` destructuring to match how `pos-profiles/index.ts` obtains them in THIS repo (read it first and copy the exact accessors).

- [ ] **Step 2: Mount + gate** — in `src/app.ts` add `.use(scaleSettings)` next to the other modules; in `src/middleware/permission.ts` add the route entry gating `PUT /api/scale-settings` on the same key item-mutation uses (`products`, action `mutate`). Read the existing map and copy an item-write entry's shape exactly.

- [ ] **Step 3: Verify wiring** — `bun run typecheck` clean; hit the routes with a smoke test if the repo has a route-test harness (mirror an existing module's route spec). Assert the permission map has the PUT entry gated to `products` mutate.

Run: `bun run typecheck && bun test src/modules/scale-settings`
Expected: clean + pass

- [ ] **Step 4: Commit**

```bash
git add src/modules/scale-settings/index.ts src/app.ts src/middleware/permission.ts src/modules/scale-settings/route.spec.ts
git commit -m "feat(scale): mount scale-settings routes, gate PUT to item-write personas"
```

---

### Task 5: electrobun — pull scale settings on Sync

**Files:**
- Create: `src/bun/sync/pull-scale-settings.ts`
- Modify: `src/bun/sync/scheduler.ts` (call it in the Sync run)
- Modify: `src/bun/scale-barcode-store.ts` (add `scaleUom` to the stored shape; keep it a cache)
- Test: `src/bun/sync/pull-scale-settings.spec.ts`

**Interfaces:**
- Consumes: `Company.custom_scale_uom`, `POS Scale Settings` from ERPNext; the device's branch (from its POS profile).
- Produces: `pullScaleSettingsOnce(): Promise<{ scaleUom: string | null; settings: ScaleBarcodeSettings }>` and persists it via the store.

- [ ] **Step 1: Write the failing test** — `pull-scale-settings.spec.ts` (mock erpnext/client + config + session like `pull-uoms.spec.ts`; fake ERPNext returns a branch row + company scale_uom):

```ts
// mock erpnextRequest to return, for POS Scale Settings, a row with
// scale_barcode_enabled=1, value_type='weight'; for Company, custom_scale_uom='Kg - X'.
// Then: expect getScaleBarcodeSettings().enabled === true and the stored scaleUom === 'Kg - X'.
```

(Model the mock and assertions on `pull-uoms.spec.ts`; the exact ERPNext paths are `/api/method/frappe.client.get_value` for Company and `/api/resource/POS Scale Settings` filtered by branch.)

- [ ] **Step 2: Run — expect FAIL** (`Cannot find module './pull-scale-settings'`).

- [ ] **Step 3: Implement `pull-scale-settings.ts`** — resolve the device's branch from its POS profile (reuse the existing profile pull's branch, or read `POS Profile.branch` via the pos-profile the session holds), then fetch Company.custom_scale_uom + the branch's POS Scale Settings via `erpnextRequest` (copy the request idiom from `pull-site-settings.ts`), normalise into `ScaleBarcodeSettings` via `normalizeScaleBarcodeSettings`, and persist with `setScaleBarcodeSettings` (now used by the sync, not the UI). Store `scaleUom` alongside.

- [ ] **Step 4: Register in `scheduler.ts`** — add `pullScaleSettingsOnce()` to the Sync sequence next to `fetchSiteSettings`.

- [ ] **Step 5: Run — expect PASS**

Run: `cd ~/Desktop/electrobun-pos && bun test src/bun/sync/pull-scale-settings.spec.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/bun/sync/pull-scale-settings.ts src/bun/sync/pull-scale-settings.spec.ts src/bun/sync/scheduler.ts src/bun/scale-barcode-store.ts
git commit -m "feat(scale): pull per-branch scale settings + balance UOM on Sync"
```

---

### Task 6: electrobun — scan guard keys on the balance UOM

**Files:**
- Modify: `src/mainview/features/register/lib/scale-scan-guard.ts`
- Modify: `src/bun/rpc/handlers.ts` (surface `scaleUom` to the guard input, alongside `stockUomWholeNumber`)
- Test: `src/mainview/features/register/lib/scale-scan-guard.test.ts`

**Interfaces:**
- Consumes: `isScaleKgUom` (Piece 1), the synced `scaleUom`, the item's `stockUom`.
- Produces: guard returns `{action:'scale', …}` iff the item's `stockUom` is the balance unit.

- [ ] **Step 1: Write the failing test** — add cases: item `stockUom = 'Kg - X'` with `scaleUom = 'Kg - X'` → scale; `stockUom = 'Piece - X'` → fallthrough; when `scaleUom` is null, fall back to `isScaleKgUom(stockUom)`.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — change the guard's weighable test from `if (item.stockUomWholeNumber) return fallthrough` to: weighable iff `scaleUom ? item.stockUom === scaleUom : isScaleKgUom(item.stockUom)`. Thread `scaleUom` into the guard input (from the synced store) via `handlers.ts`.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add src/mainview/features/register/lib/scale-scan-guard.ts src/mainview/features/register/lib/scale-scan-guard.test.ts src/bun/rpc/handlers.ts
git commit -m "feat(scale): scan guard recognises weighed items by the balance UOM"
```

---

### Task 7: electrobun — Device info shows scale settings (read-only); remove the editable dialog

**Files:**
- Modify: `src/mainview/features/settings/components/settings-device-info-card.tsx` (render synced scale settings read-only)
- Delete: `src/mainview/features/settings/components/settings-scale-barcode-dialog.tsx`
- Modify: `src/bun/rpc/handlers.ts` (remove `setScaleBarcodeSettings`; keep `getScaleBarcodeSettings`)
- Modify: any settings page that mounted the dialog (remove the entry point)
- Test: `src/mainview/features/settings/components/settings-device-info-card.test.tsx` (renders the synced values; no setter)

**Interfaces:**
- Consumes: `getScaleBarcodeSettings` (synced cache) + `scaleUom`.

- [ ] **Step 1: Write the failing test** — render the device-info card with a fake `getScaleBarcodeSettings` returning `{enabled:true, valueType:'weight', …}` + `scaleUom:'Kg'`; assert the card shows "Balances: on", "Balance unit: Kg", the barcode summary, and exposes NO edit control. (This is a React component test — run under the repo's DOM-enabled test setup.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** — add a read-only "Scale" block to `settings-device-info-card.tsx`; delete the dialog file and its mount; remove the `setScaleBarcodeSettings` RPC handler and its store export usage from the UI path.

- [ ] **Step 4: Run — expect PASS**; then `grep -rn "setScaleBarcodeSettings\|settings-scale-barcode-dialog" src` returns nothing.

- [ ] **Step 5: Commit**

```bash
git add -A src/mainview/features/settings src/bun/rpc/handlers.ts
git commit -m "feat(scale): device info shows synced scale settings read-only; drop local dialog"
```

---

## Rollout (after all tasks + Plan 2B merged)

barakat (field + doctype) → `bench migrate` each test site → proxy → electrobun build (test channel): set a branch's scale config + company Kg in the AP (Plan 2B), Sync a till, confirm Device info + a weighed scan. Then repeat to prod (skip petromall).

## Self-Review

- **Spec coverage:** Company balance UOM (T1) ✓; per-branch doctype (T2) ✓; proxy read/write + role-gate (T3–T4) ✓; till pull (T5) ✓; scan guard on balance UOM (T6) ✓; device-info read-only + remove dialog (T7) ✓. AP Units tab = **Plan 2B** (separate).
- **Placeholders:** T5–T7 steps that touch large existing React/RPC files describe the exact edit + cite the concrete pattern file to copy (pull-site-settings, pull-uoms.spec) rather than reproducing unread surrounding code; every pure-logic step (T1–T4) carries full code.
- **Type consistency:** `Barcode`/`ScaleBarcodeSettings` field names (`enabled,prefix,codeLength,valueType,valueLength,decimals`) match across proxy + electrobun; `scaleUom` naming consistent; doctype fieldnames match the proxy service reads.
