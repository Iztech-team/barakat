# Scale Config — AP "Units" Tab (Piece 2B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Admin Panel "Units" page that drives the scale-config backend built in Piece 2A: list/create the company's UOMs, set the company-wide balance UOM, and configure per-branch "has balances" + scale-barcode format — gated to item-write personas, read-only for others.

**Architecture:** One small proxy tweak (make the scale-settings GET's `branch` optional so the company balance UOM loads before a branch is picked) + AP frontend work: regenerate the proxy API types, add a `useGetUOMs` list hook and a new `scale-settings` AP API module, register a TanStack file-route + sidebar entry gated on the `products` module, and build the 3-part page reusing existing shadcn/react-hook-form/react-query patterns. The company balance UOM is saved atomically with the per-branch config via the existing `PUT /api/scale-settings` (which already sets `Company.custom_scale_uom` and upserts the branch row in one call) — no PUT change needed.

**Tech Stack:** Proxy — Bun + Elysia + TS (`bun test`). AP — React + Vite + TanStack Router + react-query + zustand + shadcn + react-hook-form + zod; i18n via 3 flat locale JSONs (en/ar/he) with RTL. AP pages have **no unit-test infra** — verify AP UI in the browser preview; unit-test only the proxy change and any extracted pure helpers.

## Global Constraints

- **Repos:** proxy = `C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat` (branch `dev`); AP = `C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/admin_panel_barakat` (branch `dev`). Both have unrelated concurrent WIP — stage only the files each task names, never `git add -A`.
- **Local ports (do not change):** proxy `8099`, AP `3000`. Start proxy first, then AP. Check a port isn't already listening before starting a second instance.
- **Persona gating:** edit controls gated on `usePermission('products').canWrite` (Manager, Branch Supervisor, Inventory Keeper). Cashier (`products:'read'`) sees the page read-only. Accountant + HR (`products:'none'`) do not see the page at all — this is deliberate and consistent with how the sidebar already hides product pages from those personas. Do NOT nest the route under `system-settings` (that guards on `module:'settings'`, which would hide the page from Branch Supervisor/Inventory Keeper).
- **Balance UOM is company-wide** (`Company.custom_scale_uom`, stored as a scoped name like `Kg - <Company>`, display-stripped in the UI); `has_balances` + barcode format are **per-branch**. Barcode field shape: `enabled, prefix, codeLength, valueType('price'|'weight'), valueLength, decimals`.
- **i18n:** every new user-facing string gets a key added to all three of `src/i18n/locales/{en,ar,he}.json`. English is authoritative; ar/he may be first-pass and will get a native review — keep them reasonable but flag them.
- The proxy already exposes: `GET/POST /api/products/uoms` (list/create UOMs, `view/mutate('products')`), `GET /api/branches` (`authenticated`), and `GET/PUT /api/scale-settings` (Piece 2A). Reuse these; do not duplicate.

---

### Task 1: proxy — make `GET /api/scale-settings` branch optional

**Files:**
- Modify: `src/modules/scale-settings/index.ts` (query schema + handler)
- Modify: `src/modules/scale-settings/service.ts` (skip the branch row when no branch)
- Modify: `src/modules/scale-settings/types.ts` (allow `branch: string | null` in the response schema)
- Test: `src/modules/scale-settings/service.spec.ts` (add a no-branch case)

**Interfaces:**
- Consumes: `getScaleSettings(erp, company, branch)` (Piece 2A).
- Produces: `getScaleSettings(erp, company, branch?: string | null)` — when `branch` is absent/empty, returns `{ scaleUom, branch: null, hasBalances: false, barcode: DEFAULT_BARCODE }` (no `POS Scale Settings` fetch).

- [ ] **Step 1: Write the failing test** — add to `service.spec.ts` (mirror the existing `fakeErp` style):

```ts
  test('getScaleSettings without a branch returns company uom + defaults, no row fetch', async () => {
    let listCalled = false
    const erp = {
      get: async () => ({ data: { custom_scale_uom: 'Kg - Beit Al-Moneh' } }),
      list: async () => { listCalled = true; return { data: [] } },
    } as never
    const res = await getScaleSettings(erp, 'Beit Al-Moneh', null)
    expect(res.scaleUom).toBe('Kg')
    expect(res.branch).toBeNull()
    expect(res.hasBalances).toBe(false)
    expect(res.barcode.enabled).toBe(false)
    expect(listCalled).toBe(false)
  })
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd "C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat" && bun test src/modules/scale-settings/service.spec.ts`
Expected: FAIL (either a type error on the `null` arg, or `listCalled === true`).

- [ ] **Step 3: Implement.** In `service.ts`, change the signature to `branch?: string | null` and short-circuit the branch row when it's falsy:

```ts
export async function getScaleSettings(erp: ERPNextClient, company: string, branch?: string | null) {
  const companyRes = await erp.get<{ custom_scale_uom?: string | null }>('Company', company)
  const scaleUom = strip(companyRes.data.custom_scale_uom, company)
  if (!branch) {
    return { scaleUom, branch: null as string | null, hasBalances: false, barcode: { ...DEFAULT_BARCODE } }
  }
  const row = await fetchRow(erp, company, branch)
  return { scaleUom, branch, hasBalances: row?.has_balances === 1, barcode: toBarcode(row) }
}
```
(Keep the existing `strip`, `fetchRow`, `toBarcode`, `DEFAULT_BARCODE`. Preserve the branch-present behaviour exactly — the existing tests must still pass.)

- [ ] **Step 4: Make the route accept no branch.** In `index.ts`, change the GET query schema to `t.Object({ branch: t.Optional(t.String()) })` and pass `query.branch ?? null`:

```ts
  .get('/', ({ erp, query, user }) => {
      if (!user.company) throw new AppError(400, 'No active company selected')
      return service.getScaleSettings(erp, user.company, query.branch ?? null)
    },
    { query: t.Object({ branch: t.Optional(t.String()) }), response: ScaleSettingsSchema })
```

- [ ] **Step 5: Allow null branch in the response schema.** In `types.ts`, update `ScaleSettingsSchema` so `branch` is `t.Union([t.String(), t.Null()])` (or `t.Nullable(t.String())` if that helper is used in the repo). Leave the rest unchanged.

- [ ] **Step 6: Run — expect PASS + typecheck**

Run: `bun test src/modules/scale-settings && bun run typecheck`
Expected: all scale-settings tests pass (existing + new); typecheck clean on the module (ignore pre-existing errors in unrelated concurrent-WIP modules).

- [ ] **Step 7: Commit** (stage only these four files):

```bash
git add src/modules/scale-settings/index.ts src/modules/scale-settings/service.ts src/modules/scale-settings/types.ts src/modules/scale-settings/service.spec.ts
git commit -m "feat(scale): scale-settings GET works without a branch (company balance UOM alone)"
```

---

### Task 2: AP — regenerate the proxy API types

**Files:**
- Modify: `src/@types/generated/api.ts` (generated — regenerated, not hand-edited)

**Interfaces:**
- Produces: generated `paths` including `/api/scale-settings` (GET now branch-optional, PUT) and `/api/products/uoms` (GET+POST), so `monolithAPI.GET('/api/scale-settings')` etc. typecheck in later tasks.

- [ ] **Step 1: Ensure the proxy is running with the new routes.** Start the proxy (from Task 1's repo) so its swagger reflects the branch-optional GET:

Run (proxy repo): check `netstat -ano | findstr ":8099 " | findstr LISTENING`; if nothing, `bun run dev` (background) and wait for it to answer `http://localhost:8099/swagger/json`.

- [ ] **Step 2: Regenerate** (AP repo). The typegen script reads `${VITE_API_BASE_URL}/swagger/json`; point it at the local proxy and run:

Run: `cd "C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/admin_panel_barakat" && VITE_API_BASE_URL=http://localhost:8099 bun run typegen`
Expected: `src/@types/generated/api.ts` updates. (KNOWN TRAP — see memory "ap-typegen-two-traps": if the script targets the wrong port it "fails", and the raw output shows a huge phantom diff until prettier-formatted.)

- [ ] **Step 3: Prettier-format the generated file** so the diff is real, not a formatting churn:

Run: `bunx prettier --write src/@types/generated/api.ts`

- [ ] **Step 4: Verify the new paths landed** — the diff should ADD `/api/scale-settings` and `/api/products/uoms` path entries and be otherwise minimal:

Run: `git diff --stat src/@types/generated/api.ts` and `grep -c "scale-settings\|products/uoms" src/@types/generated/api.ts`
Expected: the grep is ≥ 2; the stat shows a focused change (not 15k lines of formatting).

- [ ] **Step 5: Commit** (stage only the generated file):

```bash
git add src/@types/generated/api.ts
git commit -m "chore(api): regenerate proxy types (scale-settings, uoms)"
```

---

### Task 3: AP — `useGetUOMs` hook + `scale-settings` API module

**Files:**
- Modify: `src/api/products/types.ts` (confirm/extend `UOM`/`UOMsListResponse`)
- Modify: `src/api/products/requests.ts` (add `getUOMsFn`)
- Modify: `src/api/products/hooks.ts` (add `useGetUOMs`)
- Modify: `src/api/products/keys.ts` (add a query key) — if the module keys UOMs
- Create: `src/api/scale-settings/{types.ts,keys.ts,requests.ts,hooks.ts,index.ts}`
- Modify: `src/api/index.ts` (wire `scaleSettings` into the `apiRequest` object)

**Interfaces:**
- Consumes: `monolithAPI` from `src/api/openapi-instance.ts`; the generated `paths` (Task 2).
- Produces:
  - `apiRequest.products.useGetUOMs()` → `{ data: { name: string; displayName: string }[] }` (GET `/api/products/uoms`).
  - `apiRequest.scaleSettings.useGetScaleSettings(branch?: string)` → `ScaleSettings` (GET `/api/scale-settings`, branch optional).
  - `apiRequest.scaleSettings.useUpdateScaleSettings()` → mutation to `PUT /api/scale-settings`.
  - `type ScaleSettings = { scaleUom: string | null; branch: string | null; hasBalances: boolean; barcode: { enabled: boolean; prefix: string; codeLength: number; valueType: 'price' | 'weight'; valueLength: number; decimals: number } }`
  - `type UpdateScaleSettingsBody = { scaleUom: string; branch: string; hasBalances: boolean; barcode: ScaleSettings['barcode'] }`

- [ ] **Step 1: Read the pattern.** Read an existing single-object module end-to-end: `src/api/settings/{requests.ts,hooks.ts,keys.ts}` (has `useGetRoundingSettings`/`useUpdateRoundingSettings`) and `src/api/products/{requests.ts,hooks.ts}` (has `useCreateUOM`). Copy their exact idioms (how `monolithAPI.GET/PUT` is called, how `useQuery`/`useMutation` wrap the request fn, how query keys are structured, how `apiRequest` aggregates modules in `src/api/index.ts`).

- [ ] **Step 2: Add `useGetUOMs`** in the products module. `getUOMsFn`:

```ts
export async function getUOMsFn(): Promise<{ name: string; displayName: string }[]> {
  const { data } = await monolithAPI.GET('/api/products/uoms');
  return (data as unknown as { data: { name: string; displayName: string }[] }).data;
}
```
and a `useGetUOMs` `useQuery` hook using a stable query key (add one to `keys.ts` following the module's convention). Export it so it appears on `apiRequest.products`.

- [ ] **Step 3: Create the `scale-settings` module.** `types.ts` = the `ScaleSettings` + `UpdateScaleSettingsBody` types above. `keys.ts` = `scaleSettingsKeys = { all: ['scale-settings'] as const, byBranch: (b?: string) => ['scale-settings', b ?? '__company__'] as const }`. `requests.ts`:

```ts
import { monolithAPI } from '@/api/openapi-instance';
import type { ScaleSettings, UpdateScaleSettingsBody } from './types';

export async function getScaleSettingsFn(branch?: string): Promise<ScaleSettings> {
  const { data } = await monolithAPI.GET('/api/scale-settings', {
    params: { query: branch ? { branch } : {} },
  });
  return data as unknown as ScaleSettings;
}

export async function updateScaleSettingsFn(body: UpdateScaleSettingsBody): Promise<ScaleSettings> {
  const { data } = await monolithAPI.PUT('/api/scale-settings', { body });
  return data as unknown as ScaleSettings;
}
```
`hooks.ts` = `useGetScaleSettings(branch?)` (`useQuery`, key `scaleSettingsKeys.byBranch(branch)`) + `useUpdateScaleSettings()` (`useMutation`, `onSuccess` invalidates `scaleSettingsKeys.all`). `index.ts` re-exports the hooks.

- [ ] **Step 4: Wire into `apiRequest`.** In `src/api/index.ts`, import the scale-settings module and add `scaleSettings` alongside the other modules (match the existing aggregation shape exactly).

- [ ] **Step 5: Typecheck**

Run: `cd "C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/admin_panel_barakat" && bun run typecheck` (or `bunx tsc --noEmit -p tsconfig.app.json`)
Expected: clean (the generated paths from Task 2 make the `monolithAPI` calls typecheck). Ignore pre-existing unrelated errors.

- [ ] **Step 6: Commit** (stage only the api-layer files you created/edited):

```bash
git add src/api/scale-settings src/api/products/requests.ts src/api/products/hooks.ts src/api/products/keys.ts src/api/products/types.ts src/api/index.ts
git commit -m "feat(scale): AP api layer — useGetUOMs + scale-settings hooks"
```

---

### Task 4: AP — route + sidebar entry (products-gated) + nav i18n

**Files:**
- Create: `src/routes/_app/units.tsx` (a top-level route, NOT under system-settings)
- Modify: `src/constants/common/main-sidebar.tsx` (add a "Units" nav item gated `module: 'products'`)
- Modify: `src/i18n/locales/{en,ar,he}.json` (add `nav.units`)

**Interfaces:**
- Consumes: `permissionGuard`, `createFileRoute`, the page component from Task 5 (`UnitsPage`) — for this task, mount a placeholder that renders the page component name; Task 5 fills the component in. To keep this task independently testable, the route may render a minimal `<div>Units</div>` placeholder that Task 5 replaces with `<UnitsPage />`.
- Produces: a reachable `/units` route visible in the sidebar to `products:read+` personas.

- [ ] **Step 1: Add the route file** `src/routes/_app/units.tsx` (mirror `src/routes/_app/system-settings/rounding.tsx`, but guard on `products`):

```tsx
import { createFileRoute } from '@tanstack/react-router';
import { permissionGuard } from '@/utils/permission-guard';
import { RoutePendingComponent } from '@/components/common/route-pending-component';
import { RouteErrorComponent } from '@/components/common/route-error-component';
import { UnitsPage } from '@/pages/app/units/units-page';

export const Route = createFileRoute('/_app/units')({
  beforeLoad: permissionGuard({ module: 'products' }),
  staticData: { title: 'nav.units' },
  component: UnitsPage,
  pendingComponent: RoutePendingComponent,
  errorComponent: ({ error }) => <RouteErrorComponent error={error} title="Units" />,
});
```
(Confirm the exact import paths of `permissionGuard`/`RoutePendingComponent`/`RouteErrorComponent` and the `permissionGuard` argument shape by reading `system-settings.tsx` + `rounding.tsx` first; copy them verbatim. The `staticData.title` may need to be a resolved string rather than a key — match how the sibling routes do it. Until Task 5 exists, temporarily point `component` at an inline `() => <div>Units</div>` so this task builds; Task 5 swaps in `UnitsPage`.)

- [ ] **Step 2: Add the sidebar item.** In `src/constants/common/main-sidebar.tsx`, add a nav entry `{ type: 'item', title: 'nav.units', to: '/units', icon: Scale, module: 'products' }` (import a suitable lucide icon such as `Scale` or `Ruler`). Place it as a sibling near the Products group. Confirm the nav item type/shape against the existing entries.

- [ ] **Step 3: Add `nav.units`** to all three locale files: en `"Units"`, ar `"الوحدات"`, he `"יחידות"` (under the existing `nav` block).

- [ ] **Step 4: Verify build/typecheck + route generation**

Run: `bun run typecheck` (TanStack regenerates `routeTree.gen.ts` on dev/build — if a `routeTree.gen.ts` is committed, run `bun run dev` briefly or the project's route-gen command so it includes the new route, then include the regenerated file in the commit).
Expected: typecheck clean; `/units` present in `routeTree.gen.ts`.

- [ ] **Step 5: Commit**:

```bash
git add src/routes/_app/units.tsx src/constants/common/main-sidebar.tsx src/i18n/locales/en.json src/i18n/locales/ar.json src/i18n/locales/he.json src/routeTree.gen.ts
git commit -m "feat(scale): AP Units route + sidebar entry (products-gated)"
```

---

### Task 5: AP — the Units page (UOM list, balance UOM, per-branch scale panel)

**Files:**
- Create: `src/pages/app/units/units-page.tsx`
- Create (optional): `src/pages/app/units/lib/scale-barcode-preview.ts` + `.spec.ts` (a PURE helper if any non-trivial formatting is needed, unit-tested — the only unit test in this task)
- Modify: `src/routes/_app/units.tsx` (point `component` at the real `UnitsPage`)
- Modify: `src/i18n/locales/{en,ar,he}.json` (page strings)
- Reuse: `src/components/pages/products/create/add-uom-dialog.tsx` (existing create-UOM dialog) if suitable.

**Interfaces:**
- Consumes: `usePermission('products')`, `apiRequest.products.useGetUOMs`, `apiRequest.products.useCreateUOM`, `apiRequest.settings.useGetBranches`, `apiRequest.scaleSettings.useGetScaleSettings`/`useUpdateScaleSettings`, `useActiveCompanyStore`.
- Produces: the `/units` page.

- [ ] **Step 1: Read the mirrors.** Read `src/pages/app/system-settings/rounding-overview.tsx` (single-object GET/PATCH form with `canWrite` gating, `isDirty`, toast, reveal-on-condition) and `src/components/pages/settings/warehouses/*` (list + create-dialog) and `src/components/pages/reports/reports-filter-bar.tsx` (branch `<Select>`). Copy their structure.

- [ ] **Step 2: Build Part 1 — UOM list + create.** Render `useGetUOMs()` in a simple list/`DataTable` (name = `displayName`). Show the "Add unit" button + `AddUomDialog` (reuse the existing component; it already calls `useCreateUOM` and has i18n) only when `usePermission('products').canWrite`.

- [ ] **Step 3: Build Part 2 — company balance UOM picker.** A shadcn `Select` whose options are `useGetUOMs()` (value = `displayName`), current value from `useGetScaleSettings()` (no branch) `.scaleUom`. Label: "Which unit do your scales weigh in? (usually Kg)". Disabled unless `canWrite`. This value is submitted together with Part 3 (there is no company-only PUT).

- [ ] **Step 4: Build Part 3 — per-branch scale panel.** A branch `<Select>` from `useGetBranches()` (value = `branch.name`, label = `branch.branch`). On branch change, `useGetScaleSettings(selectedBranch)` loads `{ hasBalances, barcode }`. Render a `hasBalances` switch; when on, reveal the barcode-format fields (`enabled` switch, `prefix`, `codeLength`, `valueType` price/weight select, `valueLength`, `decimals`) — use the `useAutoAnimate` reveal technique from `rounding-overview.tsx`. Numeric/code inputs use `lockedDir` (LTR) per the RTL note. All inputs `disabled={!canWrite}`.

- [ ] **Step 5: Wire Save.** One Save button (shown only when `canWrite`), enabled when a branch is selected and the form is dirty. On click, call `useUpdateScaleSettings().mutate({ scaleUom: <picker value>, branch: <selected>, hasBalances, barcode })`. `toast.success` / `apiErrorMessage(err, t, ...)` with a 403 case. Invalidate the scale-settings + uoms queries via the mutation's `onSuccess` (already in the hook). Mirror `rounding-overview.tsx`'s dirty-tracking + local-state-synced-to-server-on-load approach.

- [ ] **Step 6: If you extracted a pure helper**, write its `.spec.ts` first (TDD) and run `bun test src/pages/app/units/lib/*.spec.ts`. Otherwise skip — do NOT stand up component-test infra.

- [ ] **Step 7: Point the route at `UnitsPage`** (replace the Task-4 placeholder) and add all page i18n keys to en/ar/he.

- [ ] **Step 8: Typecheck + browser verification** (this replaces unit tests for the UI):

Run: `bun run typecheck` — clean.
Then verify in the browser preview: start proxy (8099) + AP (3000); sign in; open `/units`. Confirm: (a) the UOM list shows the company's units; (b) the balance UOM picker shows the current value and its options; (c) picking a branch loads its settings; (d) toggling has-balances reveals the barcode fields; (e) Save persists (re-open the page and see the values stick); (f) as a read-only persona (Cashier) the controls are disabled and no Save button shows. Capture a screenshot of the working page.

- [ ] **Step 9: Commit**:

```bash
git add src/pages/app/units src/routes/_app/units.tsx src/i18n/locales/en.json src/i18n/locales/ar.json src/i18n/locales/he.json
git commit -m "feat(scale): AP Units page — UOMs, company balance UOM, per-branch scale config"
```

---

## Rollout (after all tasks + browser verification)

Piece 2A (barakat + proxy + POS) and Piece 2B (this) roll out together, per the 2A plan's rollout section: barakat (field + doctype) → `bench migrate` each test site → proxy (test) → AP (test) verify the Units page end-to-end → POS build (test channel) → repeat to prod (skip petromall). Bump AP + proxy versions on the deploying push per the barakat skill.

## Self-Review

- **Spec coverage** (from the 2A design doc §3 "AP — new Units tab"): UOM list + create (Task 5 Part 1, reusing existing endpoints/dialog) ✓; company balance UOM picker bound to `custom_scale_uom` (Part 2) ✓; per-branch has-balances + barcode panel calling PUT (Part 3 + Save) ✓; edit gated to Manager + item-write, read-only otherwise (persona gating throughout) ✓. The one backend gap the frontend needed — reading the company balance UOM without a branch — is Task 1.
- **Placeholders:** Task 1 (proxy) carries full code; Tasks 2–5 (AP) cite the exact mirror files to copy because reproducing unread AP framework code (TanStack route internals, the api-module aggregation, shadcn form wiring) would be guesswork — each step names the concrete pattern file and the exact hook/endpoint/props to use. UI verification is browser-based because the AP has no component-test infra.
- **Type consistency:** `ScaleSettings`/`UpdateScaleSettingsBody` barcode field names (`enabled/prefix/codeLength/valueType/valueLength/decimals`) match the proxy's `ScaleBarcodeSchema` and the POS side; `scaleUom` is the display name in the AP (stripped), scoped on write by the proxy; `branch` nullable in the GET response after Task 1.
