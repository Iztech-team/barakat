# Strict Balance Unit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** No fallbacks: a branch's scale barcodes can be ON only while the company has a balance unit; clearing the unit cascades every branch off (enforced inside ERPNext); the POS matches the chosen unit exactly or does nothing.

**Architecture:** Three enforcement layers (barakat validate/on_update hooks + consistency patch; proxy PUT validation with explicit null-clear; AP gated switch + clear-confirm + warnings) and a strict POS guard. Ships as barakat 1.2.0 / proxy 2.0.0 / AP 1.2.0 / POS 2.2.0 via the existing runbook.

**Tech Stack:** as per the repos (Frappe/Python, Bun+Elysia+TS, React, Bun+TS).

## Global Constraints

- Repos + concurrent-WIP staging rules identical to the previous plans: stage ONLY named files; `barakat/hooks.py`, `barakat/patches.txt`, AP `src/i18n/locales/ar.json` are DIRTY with other features' WIP → hunk-stage (snapshot → slice own hunks → `git apply --cached`, or the hash-object blob method if apply fails).
- Spec: `docs/superpowers/specs/2026-07-25-strict-balance-unit-design.md` (barakat repo). Warn-only on unit CHANGE; cascade only on CLEAR.
- Full proxy suite baseline 451+ tests 0 fail must stay 0 fail; POS suite 1044+ pass stays green.

---

### Task 1: barakat — validate hook, Company cascade, consistency patch

**Files:** Modify `barakat/barakat/doctype/pos_scale_settings/pos_scale_settings.py`; Create `barakat/scale_unit.py`; Modify `barakat/hooks.py` (hunk-stage); Create `barakat/patches/enforce_scale_unit_consistency.py`; Modify `barakat/patches.txt` (hunk-stage).

- [ ] Controller validate (pos_scale_settings.py):
```python
import frappe
from frappe import _
from frappe.model.document import Document


class POSScaleSettings(Document):
    def validate(self):
        if not (self.scale_barcode_enabled or self.has_balances):
            return
        uom = self.custom_company and frappe.db.get_value(
            "Company", self.custom_company, "custom_scale_uom"
        )
        if not uom:
            frappe.throw(
                _(
                    "Set the company's Scale/Balance UOM before enabling scale "
                    "barcodes for a branch."
                )
            )
```
- [ ] `barakat/scale_unit.py`:
```python
"""Company-side strictness for the scale feature: clearing the balance UOM
force-disables every branch row of that company (scoped by custom_company)."""

import frappe


def company_on_update(doc, method=None):
    if doc.get("custom_scale_uom"):
        return
    prev = doc.get_doc_before_save()
    if prev is None or not prev.get("custom_scale_uom"):
        return  # not a set->empty transition
    if not frappe.db.table_exists("POS Scale Settings"):
        return
    frappe.db.sql(
        """update `tabPOS Scale Settings`
           set scale_barcode_enabled=0, has_balances=0
           where custom_company=%s""",
        doc.name,
    )
```
- [ ] hooks.py: merge `"Company": {"on_update": "barakat.scale_unit.company_on_update"}` into the EXISTING `doc_events` dict (read it first; Company may already have entries — extend, don't replace). Hunk-stage.
- [ ] Patch `enforce_scale_unit_consistency.py`:
```python
import frappe


def execute():
    if not frappe.db.table_exists("POS Scale Settings"):
        return  # fresh site: table syncs after patches; nothing to fix
    for company in frappe.get_all("Company", pluck="name"):
        if not frappe.db.get_value("Company", company, "custom_scale_uom"):
            frappe.db.sql(
                """update `tabPOS Scale Settings`
                   set scale_barcode_enabled=0, has_balances=0
                   where custom_company=%s""",
                company,
            )
    frappe.db.commit()
```
- [ ] Append `barakat.patches.enforce_scale_unit_consistency` to patches.txt (hunk-stage).
- [ ] Verify: `python -m compileall barakat/scale_unit.py barakat/patches/enforce_scale_unit_consistency.py barakat/barakat/doctype/pos_scale_settings/pos_scale_settings.py` clean; `python -m unittest barakat.test_custom_fields barakat.test_pos_scale_settings_doctype -v` still green. Commit ONLY the five files: `feat(scale): enforce balance-unit strictness in ERPNext (validate + cascade + patch)`.

---

### Task 2: proxy — null-clear + strict PUT + e2e + smoke skip

**Files:** Modify `src/modules/scale-settings/{types.ts,service.ts,service.spec.ts,e2e.spec.ts}`, `scripts/smoke-scale-settings.mjs`.

- [ ] types.ts: `scaleUom: t.Optional(t.Union([t.String({ minLength: 1 }), t.Null()]))`.
- [ ] service.putScaleSettings:
```ts
export async function putScaleSettings(erp: ERPNextClient, company: string, body: {
  scaleUom?: string | null; branch?: string | null; hasBalances?: boolean; barcode?: Barcode
}) {
  // Effective unit AFTER this request: explicit string sets it, explicit null
  // clears it, absent keeps the company's current value.
  let effectiveUom: string | null
  if (body.scaleUom === null) effectiveUom = null
  else if (typeof body.scaleUom === 'string') effectiveUom = body.scaleUom
  else {
    const cur = await erp.get<{ custom_scale_uom?: string | null }>('Company', company)
    effectiveUom = cur.data.custom_scale_uom || null
  }

  const wantsOn = Boolean(body.barcode?.enabled || body.hasBalances)
  if (wantsOn && !effectiveUom) {
    throw new AppError(422, 'Choose a balance unit before enabling scale barcodes.')
  }

  if (body.scaleUom === null) {
    // Explicit clear — the barakat Company hook cascades the branch disable.
    await erp.update('Company', company, { custom_scale_uom: '' })
  } else if (typeof body.scaleUom === 'string') {
    const uom = await resolveScopedName(erp, 'UOM', body.scaleUom, company, 'suffixed')
    await erp.update('Company', company, { custom_scale_uom: uom })
  }
  // ...branch block unchanged...
```
- [ ] e2e additions (reuse harness): enable-without-unit → 422 nothing written; set-unit+enable one request → 200; clear+enable one request → 422; clear alone → 200 and fake company uom cleared. FIX existing tests that enabled a branch on a unit-less fake (branch-only partial, create-then-update, round-trip if needed): give the fake company `custom_scale_uom: 'Kg - Beit Al-Moneh'` up front or include scaleUom in the body — choose per test intent, keep each test's original point intact.
- [ ] service.spec.ts: same adjustment for the branch-only test; add a unit test for the 422 and for the clear path (update called with `{custom_scale_uom: ''}`).
- [ ] smoke script: before the scratch PUT, if `before.json.scaleUom == null` print `⚠ no balance unit set — strict mode blocks enabling; PUT/read-back/revert steps skipped` and DON'T count it as failure (still exit 0 if other steps passed).
- [ ] Verify: `bun test src/modules/scale-settings` green; FULL `bun test` 0 fail; `bun run typecheck` clean; `node --check` the script. Commit only these five files: `feat(scale): strict PUT — explicit unit clear, reject enable-without-unit`.

---

### Task 3: AP — gated switch, clear flow, change warning, KG note (AFTER Task 2)

**Files:** Regenerate `src/@types/generated/api.ts` (local proxy on 8099 reloads Task 2 automatically; `VITE_API_BASE_URL=http://127.0.0.1:8099 bun run typegen` + prettier — commit separately as `chore(api): regenerate proxy types (nullable scaleUom)`); Modify `src/components/pages/system-settings/edit-branch-scale-dialog.tsx`, `src/pages/app/system-settings/scale-balances-overview.tsx`, locales en/he (plain) + ar (hunk/blob-stage).

- [ ] Dialog: new prop `companyScaleUom: string | null`; the enable `Switch` gets `disabled={!canWrite || !companyScaleUom}` and, when `!companyScaleUom`, a muted hint line `t('units.enableNeedsUnit')` under the switch row. Page passes `companyScale?.scaleUom ?? null`.
- [ ] Page — balance-unit section:
  - Help text now `t('units.balanceUomHelp')` reworded to end with the strong note: EN "This should almost always be KG."
  - `Clear` button (visible when `canWrite && companyScale?.scaleUom`), opens the repo's AlertDialog/confirm pattern (find an existing destructive-confirm dialog to mirror — e.g. a delete confirmation under item-groups or warehouses; copy its component + button variants). Body: `t('units.clearUnitWarning', { count })` where `count` = number of branch rows currently enabled (from the page's `rows`). Confirm → `update.mutate({ scaleUom: null })` → success toast; queries invalidate via the hook.
  - Change warning: when `balanceUom && companyScale?.scaleUom && balanceUom !== companyScale.scaleUom`, show an inline `text-state-warning` line `t('units.changeUnitWarning')` ("Items using the old unit will stop weighing. Branches stay enabled.").
- [ ] i18n keys (en/he plain edit; ar via the established blob-stage method, keeping the other feature's `pricesUsedByTillsBadge` line unstaged): `enableNeedsUnit`, `clearUnit`, `clearUnitTitle`, `clearUnitWarning` (with `{{count}}`), `changeUnitWarning`, updated `balanceUomHelp`.
- [ ] Verify: fresh `bun run tsc` clean; JSON valid ×3. Commit: `feat(scale): strict AP — gated enable, clear-with-cascade confirm, change warning, KG note`.

---

### Task 4: POS — strict guard, drop the kg fallback

**Files:** Modify `src/mainview/features/register/lib/scale-scan-guard.ts` + its `.spec.ts`; Delete `isScaleKgUom` from `src/shared/uom.ts` (delete the file if nothing else remains/uses it — grep first) and any imports.

- [ ] Guard: `const weighable = Boolean(input.scaleUom) && item.stockUom === input.scaleUom` — null/empty `scaleUom` → fallthrough always.
- [ ] Spec: rewrite the truth table — match → add; mismatch → fallthrough; `scaleUom: null` + kg-named stockUom → **fallthrough** (the old fallback case, now inverted); keep price/stock cases.
- [ ] `grep -rn "isScaleKgUom" src` afterwards → zero hits; delete the helper (and its spec block / the file if empty).
- [ ] Verify: guard spec green; `bun test src/bun/sync/pull-scale-settings.spec.ts` green; prettier-check the touched files (release gate!); typecheck clean. Commit: `feat(scale): strict scan guard — exact balance-unit match only, no kg fallback`.

---

### Task 5 (coordinator, inline): ship

Bumps (barakat 1.2.0 `__init__.py` — may again be pre-bumped/dirty, verify diff; proxy 2.0.0; AP 1.2.0; POS 2.2.0) → push dev ×4 → promote per runbook (barakat migrate test sites, watch `enforce_scale_unit_consistency`, restart; proxy/AP test; POS build:test+release) → prod (migrate all sites EXCEPT petromall; verify bm's stale enabled row got disabled; proxy/AP main; POS prod; tags v1.2.0/v2.0.0/v1.2.0/v2.2.0) → prod smoke (expects the ⚠-skip on bm until the user sets Kg).

## Self-Review

- Spec coverage: L1 hooks/patch → T1; L2 proxy → T2; L3 AP → T3; POS strict → T4; versions/rollout → T5. Edge table: illegal-state patch (T1), scoped cascade (T1 SQL where custom_company), set+enable ordering (T2 effective-unit logic), clear+enable 422 (T2), till drift (T4 guard), warn-on-change (T3). ✓
- Placeholders: none; the two adapt-points (hooks.py doc_events merge, AP confirm-dialog mirror) name exactly what to read.
- Type consistency: `scaleUom: string | null` end to end; `custom_scale_uom: ''` as the ERPNext empty representation; cascade zeroes both flags everywhere.
