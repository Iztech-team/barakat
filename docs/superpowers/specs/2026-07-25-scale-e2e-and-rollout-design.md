# Scale Feature — E2E Tests + Rollout Runbook — Design

**Date:** 2026-07-25
**Status:** Approved (option 3: fake-ERP e2e suite + live smoke script + runbook)
**Depends on:** Piece 1 (UOM company-scoping) and Piece 2 (scale config) — both committed local on `dev`, unpushed.

## Problem

The scale feature has solid unit tests per repo, but nothing exercises the
system end to end: no test proves a real Cashier session is rejected by the
running app, the proxy's update-existing-row path is untested, the PUT schema
accepts garbage numbers (`codeLength: -3`), and there is no scripted,
petromall-safe migration procedure for the test/prod EC2s.

A hard dependency was also discovered: **the AP's unit picker filters UOMs by
`custom_company`, so on an un-migrated site it is empty.** Piece 1's
`scope_uom_company` patch must run before (or with) the scale feature — they
ship together.

## Deliverable 1 — Fake-ERP e2e suite (proxy)

New `src/modules/scale-settings/e2e.spec.ts`, plus a small schema tightening.

- **Harness:** boot the real `app` (the permission spec already proves this
  works in `bun test`) and drive it with `app.handle(new Request(...))`.
  Stub the session/auth layer at its module boundary (`mock.module`) so each
  test can mint a session for any persona (Manager, Branch Supervisor,
  Cashier, Accountant, Inventory Keeper, HR) with a fixed company. Stub the
  ERPNext client factory with an **in-memory stateful fake** (one Company doc
  with `custom_scale_uom`, a map of `POS Scale Settings` rows keyed by
  branch) so state persists across requests within a test.
- **Cases:**
  - Manager: PUT full body → 200; GET returns exactly what was saved
    (round-trip, stripped `scaleUom`).
  - Create-then-update: two PUTs for the same branch → second one UPDATES the
    existing row (fake asserts update vs create call).
  - Partial saves: `{scaleUom}` only touches Company; `{branch,...}` only
    touches the row.
  - Authorization: all 5 non-Manager personas → 403 on **GET and PUT**.
  - Scoping: branch not ending ` - <company>` → 422.
  - Validation (new bounds): empty `prefix`, `codeLength: 0`, `valueLength: 0`,
    `decimals: -1`, `decimals: 7`, bad `valueType` → 422 each.
- **Schema tightening** in `types.ts`: `prefix` minLength 1, `codeLength` ≥ 1,
  `valueLength` ≥ 1, `decimals` 0–6. (The AP dialog already enforces this
  client-side; the server must too.)

## Deliverable 2 — POS pull edge tests (electrobun)

Extend `src/bun/sync/pull-scale-settings.spec.ts`:

- No branch resolved (`branchId: null`) → persists defaults, **zero ERPNext
  calls**, returns without throwing.
- ERPNext unreachable (fetch rejects) → the pull does not corrupt the cache:
  previously synced values remain readable via `getScaleBarcodeSettings()` /
  `getScaleUom()` / `getHasBalances()`.

No AP test infra is invented — the AP repo has none; it stays typecheck +
browser verification.

## Deliverable 3 — Live smoke script (proxy)

`scripts/smoke-scale-settings.mjs`, same conventions as the existing scripts
(`verify-zero-valuation.mjs` style: env/args, plain fetch, loud exit codes).

- **Inputs:** proxy base URL, Manager email+password (proxy login flow),
  target branch name; optional Cashier credentials.
- **Steps:**
  1. **Petromall guard:** resolve the active site/company; exit(1) with a red
     message if it is petromall.
  2. Login as Manager → GET `/api/scale-settings` (no branch) → 200 proves
     the Company field exists and migrate ran.
  3. GET with the branch → 200 proves `tabPOS Scale Settings` is queryable.
  4. PUT a scratch config (enabled=true, distinctive prefix) → GET it back →
     values match → PUT the original values back (revert).
  5. If Cashier creds given: login → GET → expect **403**.
- Output: one line per step, ✅/❌, non-zero exit on any failure. Used as the
  per-site verification step in the runbook.

## Deliverable 4 — Migration runbook (barakat docs)

`docs/superpowers/2026-07-25-scale-uom-rollout-runbook.md` — the exact
operator procedure:

1. **Test env first.** Push barakat `dev → test`; SSH to the test EC2; `git
   pull` in `apps/barakat`.
2. **Migrate per named site, never `--all`:** enumerate `ls sites/`, and for
   each site **except petromall**: `bench --site <site> migrate`. The
   `scope_uom_company` patch's `SKIP_SITES` guard is the second belt if
   someone migrates petromall anyway.
3. **Verify:** patch output prints `leftover_items=0` per site; then run the
   smoke script against each site through the test proxy.
4. Push proxy `dev → test` (deploys on push), then AP, verify the Units of
   Measure + Scale & Balances pages live; POS build on the test channel; one
   till Sync + weighed-scan check.
5. **Prod:** repeat with the prod site list (explicitly named at runbook run
   time from `ls sites/`, petromall excluded), with version bumps per the
   barakat versioning rules.
6. **Rollback notes:** scale artifacts are additive (field + empty table —
   safe to leave); `scope_uom_company` is copy+repoint, so rollback = repoint
   items back (documented, not scripted — the dry run on bm de-risks this).

## Testing the deliverables themselves

- e2e suite + schema bounds: `bun test src/modules/scale-settings` all green;
  central `permission.spec.ts` still green.
- POS tests: `bun test src/bun/sync/pull-scale-settings.spec.ts` green.
- Smoke script: dry-run locally against the dev proxy (localhost:8099) with
  the local test site before it's trusted on EC2.

## Out of scope

- AP component tests (no infra in that repo).
- Automating the SSH/bench steps — the runbook is operator-run on purpose
  (prod bench access stays manual).
