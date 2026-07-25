# Scale E2E Tests + Rollout Runbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the scale feature end to end (real app, real persona tokens, fake ERPNext), harden the PUT schema, add the missing POS pull edge tests, and ship a petromall-safe smoke script + migration runbook.

**Architecture:** The proxy e2e suite boots the REAL Elysia `app` and drives it with `app.handle(new Request(...))`; identity is real (JWTs minted with the repo's own `signAccessToken`), only the ERPNext client factory is mocked (an in-memory stateful fake behind `getClientForSession`). The smoke script goes through the proxy's own auth endpoints against a live environment. The runbook is a docs-only operator procedure.

**Tech Stack:** proxy = Bun + Elysia + TS (`bun test`); electrobun = Bun + TS; scripts = plain `.mjs` (node/bun fetch); docs = markdown in the barakat repo.

## Global Constraints

- **Repos:** proxy `C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat`, electrobun `C:/Users/IzTech-OTbaileh/Desktop/electrobun-pos`, barakat `C:/Users/IzTech-OTbaileh/Desktop/bar/barakat` — all on `dev`, all with unrelated concurrent WIP: stage ONLY the files each task names, never `git add -A`.
- Scale-settings routes are Manager-only: `GET /api/scale-settings/` = `view('settings')`, `PUT` = `mutate('settings')` (already shipped — the e2e suite must PROVE it per persona, not change it).
- PUT schema bounds (new, server-side): `prefix` minLength 1, `codeLength` ≥ 1, `valueLength` ≥ 1, `decimals` 0–6. Barcode field names exactly: `enabled, prefix, codeLength, valueType('price'|'weight'), valueLength, decimals`.
- bun `mock.module` leaks across spec files in one process — every proxy task's verify step runs the FULL `bun test` (not just the new file) and must leave it as green as the pre-task baseline.
- Migration is per named site, never `bench migrate --all`; petromall is excluded by the runbook's site list AND by `scope_uom_company`'s `SKIP_SITES`.

---

### Task 1: proxy — e2e harness + happy-path flows

**Files:**
- Create: `src/modules/scale-settings/e2e.spec.ts`

**Interfaces:**
- Consumes: `app` (`src/app.ts`), `signAccessToken` + `TokenPayload` (`src/lib/jwt.ts:11,30`), `getClientForSession` (`src/lib/erpnext.ts`), `ROLE_CATALOG` personas.
- Produces: the harness helpers (`mintToken(persona)`, `makeFakeErp()`, `call(method, path, token, body?)`) that Task 2 extends IN THE SAME FILE.

- [ ] **Step 1: Read the real shapes first.** Read `src/lib/jwt.ts` (the full `TokenPayload` interface — the fields below must match it exactly), `src/middleware/site.ts` (already known: requires `siteUrl` + `clientSid`, provides `erp` via `getClientForSession(siteUrl, clientSid)`), and `src/middleware/permission.ts:380-395` (persona read from `token.rolePreset`). Confirm how `env.JWT_SECRET` loads in tests (`src/config` / `.env` — bun auto-loads `.env`; if the repo's `.env` lacks `JWT_SECRET`, set `process.env.JWT_SECRET = 'e2e-test-secret'` at the very top of the spec, before any import that reads env).

- [ ] **Step 2: Write the failing spec** — `src/modules/scale-settings/e2e.spec.ts`:

```ts
import { afterAll, beforeAll, describe, expect, it, mock } from 'bun:test'

// ── Stateful in-memory fake ERPNext ─────────────────────────────────────
// One Company doc + POS Scale Settings rows keyed by branch. Records
// whether the row write was an update or a create so the upsert path is
// provable.
const COMPANY = 'Beit Al-Moneh'
const BRANCH = `Test Branch - ${COMPANY}`

function makeFakeErp() {
  const state = {
    company: { custom_scale_uom: null as string | null },
    rows: new Map<string, Record<string, unknown>>(),
    lastRowWrite: null as 'create' | 'update' | null,
  }
  const erp = {
    get: async (dt: string, _name: string) => ({
      data: dt === 'Company' ? { ...state.company } : {},
    }),
    list: async (dt: string, params: { filters?: string }) => {
      if (dt !== 'POS Scale Settings') return { data: [] }
      const filters = JSON.parse(params.filters ?? '[]') as [string, string, string][]
      const branch = filters.find((f) => f[0] === 'branch')?.[2]
      const row = branch ? state.rows.get(branch) : undefined
      return { data: row ? [{ name: branch, ...row }] : [] }
    },
    update: async (dt: string, name: string, doc: Record<string, unknown>) => {
      if (dt === 'Company') state.company = { ...state.company, ...doc } as never
      else {
        state.rows.set(name, { ...state.rows.get(name), ...doc })
        state.lastRowWrite = 'update'
      }
      return { data: {} }
    },
    create: async (_dt: string, doc: Record<string, unknown>) => {
      state.rows.set(doc.branch as string, doc)
      state.lastRowWrite = 'create'
      return { data: { name: doc.branch } }
    },
  }
  return { erp, state }
}

let fake = makeFakeErp()

// Mock ONLY the client factory; keep every other export real. mock.restore()
// in afterAll so later spec files see the real module.
import * as erpnextActual from '../../lib/erpnext'
mock.module('../../lib/erpnext', () => ({
  ...erpnextActual,
  getClientForSession: () => fake.erp,
}))

// Import AFTER the mock so app + siteGuard capture the mocked factory.
const { app } = await import('../../app')
const { signAccessToken } = await import('../../lib/jwt')

// ── Identity: REAL tokens, one per persona ──────────────────────────────
async function mintToken(rolePreset: string | null) {
  return signAccessToken({
    // Align these fields with the actual TokenPayload interface (jwt.ts:11):
    sub: 'e2e@test.local',
    email: 'e2e@test.local',
    rolePreset,
    roles: [],
    company: COMPANY,
    siteId: 'e2e-site',
    siteUrl: 'https://e2e.invalid',
    clientSid: 'e2e-sid',
  } as never)
}

async function call(method: string, path: string, token: string, body?: unknown) {
  return app.handle(
    new Request(`http://local${path}`, {
      method,
      headers: {
        authorization: `Bearer ${token}`,
        ...(body ? { 'content-type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    }),
  )
}

const GOOD_BODY = {
  scaleUom: 'Kg',
  branch: BRANCH,
  hasBalances: true,
  barcode: { enabled: true, prefix: '2', codeLength: 7, valueType: 'weight' as const, valueLength: 5, decimals: 2 },
}

afterAll(() => mock.restore())

describe('scale-settings e2e (real app, fake erp)', () => {
  let manager: string
  beforeAll(async () => {
    manager = await mintToken('Manager')
  })

  it('Manager round-trip: PUT saves, GET returns exactly what was saved (stripped uom)', async () => {
    fake = makeFakeErp()
    const put = await call('PUT', '/api/scale-settings/', manager, GOOD_BODY)
    expect(put.status).toBe(200)
    const get = await call('GET', `/api/scale-settings/?branch=${encodeURIComponent(BRANCH)}`, manager)
    expect(get.status).toBe(200)
    const body = (await get.json()) as typeof GOOD_BODY & { scaleUom: string }
    expect(body.scaleUom).toBe('Kg')
    expect(body.hasBalances).toBe(true)
    expect(body.barcode).toEqual(GOOD_BODY.barcode)
  })

  it('second PUT for the same branch UPDATES the existing row (no duplicate create)', async () => {
    fake = makeFakeErp()
    await call('PUT', '/api/scale-settings/', manager, GOOD_BODY)
    expect(fake.state.lastRowWrite).toBe('create')
    await call('PUT', '/api/scale-settings/', manager, {
      ...GOOD_BODY,
      barcode: { ...GOOD_BODY.barcode, prefix: '9' },
    })
    expect(fake.state.lastRowWrite).toBe('update')
    expect(fake.state.rows.size).toBe(1)
    expect(fake.state.rows.get(BRANCH)?.scale_barcode_prefix).toBe('9')
  })

  it('partial PUT {scaleUom} touches only the Company', async () => {
    fake = makeFakeErp()
    const res = await call('PUT', '/api/scale-settings/', manager, { scaleUom: 'Kg' })
    expect(res.status).toBe(200)
    expect(fake.state.company.custom_scale_uom).toBe(`Kg - ${COMPANY}`)
    expect(fake.state.rows.size).toBe(0)
  })

  it('partial PUT {branch,...} touches only the row', async () => {
    fake = makeFakeErp()
    const { scaleUom: _omit, ...branchOnly } = GOOD_BODY
    const res = await call('PUT', '/api/scale-settings/', manager, branchOnly)
    expect(res.status).toBe(200)
    expect(fake.state.company.custom_scale_uom).toBeNull()
    expect(fake.state.rows.size).toBe(1)
  })

  it('GET without a branch returns the company uom + defaults', async () => {
    fake = makeFakeErp()
    fake.state.company.custom_scale_uom = `Kg - ${COMPANY}`
    const res = await call('GET', '/api/scale-settings/', manager)
    expect(res.status).toBe(200)
    const body = (await res.json()) as { scaleUom: string; branch: null; barcode: { enabled: boolean } }
    expect(body.scaleUom).toBe('Kg')
    expect(body.branch).toBeNull()
    expect(body.barcode.enabled).toBe(false)
  })
})
```

NOTE for the implementer: the exact `TokenPayload` field names (Step 1) govern — if the interface says e.g. `preset` instead of `rolePreset` or requires extra fields, adapt `mintToken` (the permission middleware reads `token.rolePreset` per `permission.ts:388`, so that one is certain). If `resolveScopedName` inside `putScaleSettings` calls the erp (`get`/`list` on UOM), the fake's generic `get` returns `{}` and `list` returns `[]` for non-scale doctypes — confirm that yields the suffixed `Kg - Beit Al-Moneh` fallback (the existing unit test proves this exact behavior with the same shape of fake).

- [ ] **Step 3: Run — expect FAIL/ERROR** (file compiles as tests run; failures acceptable only in the new file):

Run: `cd "C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat" && bun test src/modules/scale-settings/e2e.spec.ts`

- [ ] **Step 4: Fix the harness until all 5 pass.** No production code changes are expected in this task — if a test exposes a real service bug, STOP and report DONE_WITH_CONCERNS with the failing case.

- [ ] **Step 5: Full-suite regression check** (mock leak guard):

Run: `bun test 2>&1 | tail -5` — same pass/fail counts as the pre-task baseline (record the baseline first with `git stash list >/dev/null; bun test 2>&1 | tail -3` before creating the file if unsure).

- [ ] **Step 6: Commit**

```bash
git add src/modules/scale-settings/e2e.spec.ts
git commit -m "test(scale): e2e harness — real app + tokens, fake ERPNext, happy paths"
```

---

### Task 2: proxy — authorization + validation e2e, schema bounds

**Files:**
- Modify: `src/modules/scale-settings/types.ts` (tighten `ScaleBarcodeSchema`)
- Modify: `src/modules/scale-settings/e2e.spec.ts` (append two describes)

**Interfaces:**
- Consumes: Task 1's `mintToken`, `call`, `GOOD_BODY`, `makeFakeErp` (same file).
- Produces: server-side bounds — `prefix` minLength 1, `codeLength` ≥ 1, `valueLength` ≥ 1, `decimals` 0–6.

- [ ] **Step 1: Write the failing tests** — append to `e2e.spec.ts`:

```ts
describe('scale-settings e2e — authorization', () => {
  const DENIED = ['Branch Supervisor', 'Cashier', 'Accountant', 'Inventory Keeper', 'HR']

  for (const persona of DENIED) {
    it(`${persona}: GET → 403`, async () => {
      const token = await mintToken(persona)
      const res = await call('GET', '/api/scale-settings/', token)
      expect(res.status).toBe(403)
    })

    it(`${persona}: PUT → 403 and writes nothing`, async () => {
      fake = makeFakeErp()
      const token = await mintToken(persona)
      const res = await call('PUT', '/api/scale-settings/', token, GOOD_BODY)
      expect(res.status).toBe(403)
      expect(fake.state.rows.size).toBe(0)
      expect(fake.state.company.custom_scale_uom).toBeNull()
    })
  }

  it('no token → 401', async () => {
    const res = await app.handle(new Request('http://local/api/scale-settings/'))
    expect(res.status).toBe(401)
  })

  it('foreign-company branch → 422, nothing written', async () => {
    fake = makeFakeErp()
    const manager = await mintToken('Manager')
    const res = await call('PUT', '/api/scale-settings/', manager, {
      ...GOOD_BODY,
      branch: 'Some Branch - Other Co',
    })
    expect(res.status).toBe(422)
    expect(fake.state.rows.size).toBe(0)
  })
})

describe('scale-settings e2e — body validation (schema bounds)', () => {
  const bad = (patch: Partial<typeof GOOD_BODY.barcode>) => ({
    ...GOOD_BODY,
    barcode: { ...GOOD_BODY.barcode, ...patch },
  })

  const CASES: [string, unknown][] = [
    ['empty prefix', bad({ prefix: '' })],
    ['codeLength 0', bad({ codeLength: 0 })],
    ['negative codeLength', bad({ codeLength: -3 })],
    ['valueLength 0', bad({ valueLength: 0 })],
    ['decimals -1', bad({ decimals: -1 })],
    ['decimals 7', bad({ decimals: 7 })],
    ['bad valueType', bad({ valueType: 'weightz' as never })],
  ]

  for (const [label, body] of CASES) {
    it(`${label} → 422, nothing written`, async () => {
      fake = makeFakeErp()
      const manager = await mintToken('Manager')
      const res = await call('PUT', '/api/scale-settings/', manager, body)
      expect(res.status).toBe(422)
      expect(fake.state.rows.size).toBe(0)
    })
  }
})
```

(If Elysia returns 400 rather than 422 for schema failures in this repo — check how other modules' validation errors surface, e.g. an existing spec or the error mapper in `src/lib/errors.ts` — assert that repo-consistent status instead, in ALL the validation cases including the existing foreign-branch 422.)

- [ ] **Step 2: Run — the 7 validation cases FAIL** (schema still loose), authorization cases should already pass:

Run: `bun test src/modules/scale-settings/e2e.spec.ts`

- [ ] **Step 3: Tighten the schema** in `types.ts`:

```ts
export const ScaleBarcodeSchema = t.Object({
  enabled: t.Boolean(),
  prefix: t.String({ minLength: 1 }),
  codeLength: t.Number({ minimum: 1 }),
  valueType: t.Union([t.Literal('price'), t.Literal('weight')]),
  valueLength: t.Number({ minimum: 1 }),
  decimals: t.Number({ minimum: 0, maximum: 6 }),
})
```

- [ ] **Step 4: Run — all e2e green**, then the module + full suite:

Run: `bun test src/modules/scale-settings && bun run typecheck && bun test 2>&1 | tail -3`
Expected: module green, typecheck clean, full suite matches baseline.

- [ ] **Step 5: Commit**

```bash
git add src/modules/scale-settings/e2e.spec.ts src/modules/scale-settings/types.ts
git commit -m "test(scale): e2e authz + validation; enforce barcode bounds server-side"
```

---

### Task 3: electrobun — POS pull edge tests

**Files:**
- Modify: `src/bun/sync/pull-scale-settings.spec.ts` (append two tests)

**Interfaces:**
- Consumes: the existing spec's mock setup (`mock.module` of `../erpnext/config` + `../erpnext/client`, `BARAKAT_SCALE_BARCODE_PREFS_PATH` temp-file override, dynamic `await import('./pull-scale-settings')`).

- [ ] **Step 1: Read the existing spec** to reuse its exact mock/env pattern, then append:

```ts
test('no branch resolved → persists defaults, zero ERPNext calls', async () => {
  let calls = 0
  mock.module('../erpnext/config', () => ({
    getErpnextConfig: () => ({ ...makeTestErpnextConfig(), branchId: null }),
    isErpnextConfigured: () => true,
    getCachedConfigForDebug: () => makeTestErpnextConfig(),
  }))
  mock.module('../erpnext/client', () => ({
    erpnextRequest: async () => { calls++; return { data: [] } },
  }))
  const { pullScaleSettingsOnce } = await import('./pull-scale-settings')
  const res = await pullScaleSettingsOnce()
  expect(calls).toBe(0)
  expect(res.scaleUom).toBeNull()
  const { getScaleBarcodeSettings, getHasBalances } = await import('../scale-barcode-store')
  expect(getScaleBarcodeSettings().enabled).toBe(false)
  expect(getHasBalances()).toBe(false)
})

test('ERPNext unreachable → previously synced cache survives', async () => {
  // 1st pull succeeds (reuse the happy-path mocks from the first test:
  // row with scale_barcode_enabled=1 + company custom_scale_uom).
  // 2nd pull: erpnextRequest rejects.
  mock.module('../erpnext/client', () => ({
    erpnextRequest: async () => { throw new Error('ECONNREFUSED') },
  }))
  const { pullScaleSettingsOnce } = await import('./pull-scale-settings')
  await pullScaleSettingsOnce().catch(() => {})
  const { getScaleBarcodeSettings, getScaleUom } = await import('../scale-barcode-store')
  // the cache still holds the 1st pull's values
  expect(getScaleBarcodeSettings().enabled).toBe(true)
  expect(getScaleUom()).toBe('Kg - Beit Al-Moneh')
})
```

Adapt the two snippets to the file's real helper names and its happy-path mock (the row/company fixtures already exist in test 1 — the survive-test seeds by literally running the happy-path pull first, THEN swapping the client mock to the rejecting one). If `pullScaleSettingsOnce` internally catches fetch errors and persists defaults (overwriting the cache), that is a REAL bug per the spec ("does not corrupt the cache") — report DONE_WITH_CONCERNS with the observed behavior instead of changing the test to match.

- [ ] **Step 2: Run — expect the new tests to fail or pass depending on current behavior; investigate any failure honestly:**

Run: `cd "C:/Users/IzTech-OTbaileh/Desktop/electrobun-pos" && bun test src/bun/sync/pull-scale-settings.spec.ts`

- [ ] **Step 3: If (and only if) the unreachable-case overwrites the cache**, fix `pull-scale-settings.ts` minimally: wrap the two fetches so a thrown/failed fetch returns EARLY without calling any store setter (preserving the prior cache), rather than persisting defaults. The no-branch case must still persist defaults (that's intentional). Re-run until green.

- [ ] **Step 4: Full-file green + typecheck; commit** (stage only what you touched):

```bash
git add src/bun/sync/pull-scale-settings.spec.ts src/bun/sync/pull-scale-settings.ts
git commit -m "test(scale): pull edge cases — no branch, ERPNext down keeps cache"
```

(Drop `pull-scale-settings.ts` from the add if Step 3 wasn't needed.)

---

### Task 4: proxy — live smoke script

**Files:**
- Create: `scripts/smoke-scale-settings.mjs`

**Interfaces:**
- Consumes: proxy auth endpoints `POST /api/auth/login`, `POST /api/auth/set-site` (read `src/modules/auth/index.ts:14-56` for exact request/response bodies), `GET/PUT /api/scale-settings/`.

- [ ] **Step 1: Read the conventions.** Read `scripts/verify-zero-valuation.mjs` (env-var config style, section comments, exit codes) and `src/modules/auth/index.ts` (login body → `{ accessToken?, sites? }` shapes — use the REAL field names).

- [ ] **Step 2: Write the script** — `scripts/smoke-scale-settings.mjs`:

```js
// Post-migration smoke test for the scale feature. Run once per environment
// AFTER `bench migrate`. Goes through the proxy exactly like the AP does.
//
//   PROXY=http://localhost:8099 USR=manager@x PWD=... BRANCH="Test Branch - Co" \
//     node scripts/smoke-scale-settings.mjs
//   Optional: CASHIER_USR / CASHIER_PWD to prove the 403.
//   Optional: SITE=<siteId> to pick a site when the account has several.
//
// Exits non-zero on the first failure. REFUSES to run against petromall.

const PROXY = process.env.PROXY || 'http://localhost:8099';
const USR = process.env.USR;
const PWD = process.env.PWD;
const BRANCH = process.env.BRANCH;
const SITE = process.env.SITE || '';
const CASHIER_USR = process.env.CASHIER_USR || '';
const CASHIER_PWD = process.env.CASHIER_PWD || '';

if (!USR || !PWD || !BRANCH) {
  console.error('Usage: PROXY=.. USR=.. PWD=.. BRANCH=".." node scripts/smoke-scale-settings.mjs');
  process.exit(1);
}

let failed = false;
const ok = (msg) => console.log(`  \u2705 ${msg}`);
const bad = (msg) => { console.error(`  \u274C ${msg}`); failed = true; };

async function api(method, path, token, body) {
  const res = await fetch(`${PROXY}${path}`, {
    method,
    headers: {
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(body ? { 'content-type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = null;
  try { json = await res.json(); } catch {}
  return { status: res.status, json };
}

async function loginAndPickSite(usr, pwd) {
  // 1) login  2) pick site  — adapt field names to the auth module's real
  // response shapes (read src/modules/auth/index.ts before finalizing).
  const login = await api('POST', '/api/auth/login', null, { email: usr, password: pwd });
  if (login.status !== 200) throw new Error(`login failed (${login.status})`);
  const sites = login.json.sites ?? [];
  const site = SITE ? sites.find((s) => s.id === SITE || s.siteId === SITE) : sites[0];
  if (!site) throw new Error('no site available for this account');
  const siteId = site.id ?? site.siteId;
  // ── PETROMALL GUARD ──
  const label = JSON.stringify(site).toLowerCase();
  if (label.includes('petromall')) {
    console.error('\u26D4 target site resolves to PETROMALL — refusing to run.');
    process.exit(1);
  }
  const set = await api('POST', '/api/auth/set-site', login.json.accessToken, { siteId });
  if (set.status !== 200) throw new Error(`set-site failed (${set.status})`);
  return set.json.accessToken;
}

console.log(`Smoke: scale-settings via ${PROXY}`);
const token = await loginAndPickSite(USR, PWD);

// 1. Company field exists (GET no branch = reads Company.custom_scale_uom)
{
  const r = await api('GET', '/api/scale-settings/', token);
  r.status === 200 ? ok('GET company-level → 200 (Company.custom_scale_uom exists)')
                   : bad(`GET company-level → ${r.status}`);
}

// 2. Branch table queryable
const before = await api('GET', `/api/scale-settings/?branch=${encodeURIComponent(BRANCH)}`, token);
before.status === 200 ? ok('GET branch → 200 (tabPOS Scale Settings queryable)')
                      : bad(`GET branch → ${before.status}`);

// 3. PUT scratch config → read back → revert
if (before.status === 200) {
  const scratch = {
    scaleUom: before.json.scaleUom ?? 'Kg',
    branch: BRANCH,
    hasBalances: true,
    barcode: { enabled: true, prefix: '8', codeLength: 7, valueType: 'price', valueLength: 5, decimals: 2 },
  };
  const put = await api('PUT', '/api/scale-settings/', token, scratch);
  put.status === 200 ? ok('PUT scratch config → 200') : bad(`PUT → ${put.status}`);
  const after = await api('GET', `/api/scale-settings/?branch=${encodeURIComponent(BRANCH)}`, token);
  after.json?.barcode?.prefix === '8' ? ok('read-back matches (prefix 8)')
                                      : bad(`read-back mismatch: ${JSON.stringify(after.json?.barcode)}`);
  // revert to the original values
  const revert = await api('PUT', '/api/scale-settings/', token, {
    scaleUom: before.json.scaleUom ?? 'Kg',
    branch: BRANCH,
    hasBalances: before.json.hasBalances,
    barcode: before.json.barcode,
  });
  revert.status === 200 ? ok('reverted to original') : bad(`revert → ${revert.status}`);
}

// 4. Optional: cashier is locked out
if (CASHIER_USR && CASHIER_PWD) {
  const cashier = await loginAndPickSite(CASHIER_USR, CASHIER_PWD);
  const r = await api('GET', '/api/scale-settings/', cashier);
  r.status === 403 ? ok('Cashier GET → 403 (Manager-only confirmed)')
                   : bad(`Cashier GET → ${r.status} (expected 403)`);
}

process.exit(failed ? 1 : 0);
```

- [ ] **Step 3: Dry-run locally** against the dev proxy + local test site (proxy already running on 8099):

Run: `cd "C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat" && PROXY=http://localhost:8099 USR=<manager email> PWD=<pwd> BRANCH="<a real branch>" node scripts/smoke-scale-settings.mjs`
Expected: NOTE — locally the ERPNext site is NOT migrated yet, so steps 1-3 may legitimately fail on missing field/table; what MUST work is: login flow, petromall guard logic (test it by forcing `SITE=petromall-like` if available, else code-review it), clean ✅/❌ output, non-zero exit on failure. Report the observed output verbatim.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke-scale-settings.mjs
git commit -m "chore(scale): post-migration smoke script (petromall-guarded)"
```

---

### Task 5: barakat — migration runbook

**Files:**
- Create: `docs/superpowers/2026-07-25-scale-uom-rollout-runbook.md`

- [ ] **Step 1: Write the runbook** with EXACTLY this structure (fill the command blocks verbatim; the operator only ever copy-pastes):

```markdown
# Scale + UOM-Scoping Rollout Runbook (test → prod, petromall excluded)

Ships together: Piece 1 (UOM company-scoping, patch `scope_uom_company`) +
Piece 2 (scale config). The AP unit picker is EMPTY until Piece 1's patch
runs — never ship the AP/proxy without migrating barakat first.

## 0. Preconditions
- [ ] All four repos' `dev` pushed (barakat, proxy, AP, electrobun).
- [ ] Versions bumped per the barakat versioning rules BEFORE promoting.

## 1. TEST environment
1. Promote barakat: `git checkout test && git merge dev && git push origin test` (repeat convention used by the other repos).
2. SSH to the test EC2 (see the barakat skill for host/keys).
3. `cd <bench>/apps/barakat && git pull` — verify HEAD advanced to the promoted commit (`git log -1`).
4. Enumerate sites: `ls <bench>/sites` — write the list here at run time.
5. For EACH site EXCEPT petromall:
   `bench --site <site> migrate`
   - Watch for the patch line: `scope_uom_company ... leftover_items=0`.
   - The patch skips petromall by name even if run (SKIP_SITES) — the site
     list exclusion is belt one, SKIP_SITES is belt two.
6. Promote proxy `dev → test` (push deploys). Verify the deployed version
   (see the barakat skill's "what's live" checks).
7. Smoke each site:
   `PROXY=<test proxy url> USR=<manager> PWD=<pwd> BRANCH="<branch>" node scripts/smoke-scale-settings.mjs`
   All ✅ required before continuing.
8. Promote AP `dev → test`; open the AP: Products → Units of Measure shows
   the site's units; Settings → Scale & Balances loads; a non-Manager
   cannot see either the page or the API (spot-check one Cashier login).
9. POS: build + release on the TEST channel; on one till: Sync, check
   Settings → Device info shows the synced scale values; scan one weighed
   barcode on a Kg item.

## 2. PROD environment
Repeat 1-9 with: prod EC2, prod proxy/AP URLs, the prod site list from
`ls sites` (EXCLUDE petromall), and a prod POS release. Do bm.iztech.net
first (fresh customer, lowest risk), then the remaining sites.

## 3. Rollback
- Scale artifacts are additive (Company field + empty table): safe to leave
  in place; disable by simply not configuring branches.
- `scope_uom_company` is copy+repoint (originals are NOT deleted): rollback
  = repoint `Item.stock_uom` / `UOM Conversion Detail` / Item Prices back to
  the bare names and clear `Company.custom_scale_uom`. The bm dry run
  (38 units / 1021 items / 1694 prices) is the reference for expected scale.
- Proxy/AP: revert = push the previous commit to the same branch (push IS
  deploy). POS: previous release remains installable from the release page.
```

- [ ] **Step 2: Sanity-check against the barakat skill** (`~/.claude/skills/barakat/SKILL.md`): branch-promotion commands, deploy-on-push facts, POS release flow, and "what's live" verification must match what the skill says — correct the runbook where they differ (the skill is the source of truth).

- [ ] **Step 3: Commit** (barakat repo, stage only this file):

```bash
git add docs/superpowers/2026-07-25-scale-uom-rollout-runbook.md
git commit -m "docs(scale): rollout runbook — test→prod, per-site migrate, petromall excluded"
```

---

## Self-Review

- **Spec coverage:** D1 e2e suite → Tasks 1-2 (harness+happy, authz+validation+bounds) ✓; D2 POS edge tests → Task 3 ✓; D3 smoke script → Task 4 ✓; D4 runbook → Task 5 ✓.
- **Placeholders:** none — every step carries real code; the two adapt-to-repo notes (TokenPayload fields, auth response shapes) name the exact file+line to read and what is already certain.
- **Type consistency:** `mintToken`/`call`/`GOOD_BODY`/`makeFakeErp` defined in Task 1, reused in Task 2 in the same file; barcode field names match the shipped `ScaleBarcodeSchema`; store accessors in Task 3 match `scale-barcode-store.ts` exports.
