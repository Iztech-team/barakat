# UOM Company Scoping (Piece 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each company its own isolated set of Units of Measure (`custom_company` + ` - <Company>` name suffix), migrate every existing item onto its company's scoped units, and scope the AP picker — without breaking the electrobun scale flow.

**Architecture:** Three repos ship in order. (1) barakat app adds the `custom_company` field to UOM and a data-migration patch that scopes each company's units. (2) proxy filters/creates/resolves UOMs by company. (3) electrobun gains a suffix-tolerant `isScaleKgUom` helper. The migration is what makes picker-filtering safe — it guarantees no item is left on a global unit.

**Tech Stack:** Frappe/ERPNext (Python) for barakat; Bun + TypeScript + Elysia for proxy; Bun + TypeScript for electrobun. Tests: `python -m unittest` (barakat), `bun test` (proxy/electrobun).

## Global Constraints

- Suffix token is the **Company `name`** (e.g. `Kg - Beit Al-Moneh`), never the abbr — the proxy strips using the active company name.
- Suffix format is exactly `` `${uom} - ${company}` `` (space-hyphen-space). Idempotent: never double-suffix an already-` - <company>` name.
- The migration patch is **idempotent** and **skips `petromall.iztech.net`** (and any site with no Company).
- Post-condition per company: **zero** of its items / item-UOM rows / item prices reference a unit lacking the ` - <company>` suffix.
- Never blank an unmatched marker — count and report it.
- Ship order: **barakat (field+patch) → proxy → electrobun**; every **test** site before any **prod** site.

---

### Task 1: barakat — `custom_company` field on UOM

**Files:**
- Modify: `barakat/fixtures/custom_field.json` (append one entry)
- Test: `barakat/test_custom_fields.py` (add one test)

**Interfaces:**
- Produces: a Custom Field `UOM-custom_company` (Link→Company) that the proxy's ownership + filtering rely on.

- [ ] **Step 1: Write the failing test**

Add to `barakat/test_custom_fields.py` inside `CompanyMarkersAreEnforceable`:

```python
    def test_uom_has_enforceable_company_marker(self):
        f = _by_name(self.rows, "UOM-custom_company")
        self.assertIsNotNone(f, "UOM must carry a custom_company marker")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Company")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/bar/barakat && python -m unittest barakat.test_custom_fields -v`
Expected: FAIL — `AssertionError: UOM must carry a custom_company marker`

- [ ] **Step 3: Add the fixture entry**

Append to the array in `barakat/fixtures/custom_field.json` (mirror the Item Group entry):

```json
{
 "doctype": "Custom Field",
 "name": "UOM-custom_company",
 "dt": "UOM",
 "fieldname": "custom_company",
 "label": "Company (Barakat)",
 "fieldtype": "Link",
 "options": "Company",
 "insert_after": "enabled",
 "in_list_view": 0,
 "in_standard_filter": 1
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest barakat.test_custom_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add barakat/fixtures/custom_field.json barakat/test_custom_fields.py
git commit -m "feat(uom): add custom_company marker field to UOM"
```

---

### Task 2: barakat — migration patch `scope_uom_company`

**Files:**
- Create: `barakat/patches/scope_uom_company.py`
- Create: `barakat/patches/_uom_scope_logic.py` (pure, Frappe-free classification — unit-tested)
- Create: `barakat/test_uom_scope_logic.py`
- Modify: `barakat/patches.txt` (register)

**Interfaces:**
- Consumes: `UOM-custom_company` from Task 1.
- Produces: after `bench migrate`, every company's items/prices/UOM-rows reference a ` - <company>` unit; a printed count report.

- [ ] **Step 1: Write the failing test for the pure classifier**

Create `barakat/test_uom_scope_logic.py`:

```python
import unittest
from barakat.patches._uom_scope_logic import classify_unit, scoped_name

class ScopedName(unittest.TestCase):
    def test_appends_once(self):
        self.assertEqual(scoped_name("Kg", "Beit Al-Moneh"), "Kg - Beit Al-Moneh")
    def test_idempotent(self):
        self.assertEqual(scoped_name("Kg - Beit Al-Moneh", "Beit Al-Moneh"), "Kg - Beit Al-Moneh")

class ClassifyUnit(unittest.TestCase):
    # rename-safe: no custom_company yet AND only this company references it
    def test_rename_safe(self):
        self.assertEqual(
            classify_unit("Bag", owned_company=None, referencing_companies={"C"}, company="C"),
            "rename",
        )
    # shared: another company also uses it
    def test_shared_multi_company(self):
        self.assertEqual(
            classify_unit("Kg", owned_company=None, referencing_companies={"C", "D"}, company="C"),
            "copy",
        )
    # shared: it's a built-in already owned by nobody but is a system default in use elsewhere -> copy is forced by presence of other refs; when only C refs but we still must not rename a globally-owned one:
    def test_already_owned_by_other(self):
        self.assertEqual(
            classify_unit("Kg", owned_company="D", referencing_companies={"C"}, company="C"),
            "copy",
        )
    def test_already_scoped_to_us(self):
        self.assertEqual(
            classify_unit("Kg - C", owned_company="C", referencing_companies={"C"}, company="C"),
            "skip",
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest barakat.test_uom_scope_logic -v`
Expected: FAIL — `ModuleNotFoundError: barakat.patches._uom_scope_logic`

- [ ] **Step 3: Implement the pure classifier**

Create `barakat/patches/_uom_scope_logic.py`:

```python
"""Pure decision logic for UOM scoping — no Frappe imports, unit-tested."""

SEP = " - "

def scoped_name(uom, company):
    suffix = f"{SEP}{company}"
    return uom if uom.endswith(suffix) else f"{uom}{suffix}"

def classify_unit(uom, owned_company, referencing_companies, company):
    """Return 'skip' | 'rename' | 'copy' for scoping `uom` to `company`.

    - skip   : already scoped to us.
    - rename : safe to rename in place (nobody owns it, only we reference it).
    - copy   : shared/foreign — create a scoped copy and repoint our refs.
    """
    if uom.endswith(f"{SEP}{company}") and owned_company == company:
        return "skip"
    only_us = referencing_companies == {company} or referencing_companies == set()
    if owned_company is None and only_us:
        return "rename"
    return "copy"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest barakat.test_uom_scope_logic -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Implement the Frappe patch**

Create `barakat/patches/scope_uom_company.py`:

```python
"""Scope each company's Units of Measure: custom_company + ' - <Company>' name.

Data-driven off CURRENT references (Item.stock_uom, the Item UOM child table, and
Item Price.uom). Rename-safe units are renamed in place (ERPNext cascades every
Link); shared/built-in units get a scoped copy and an explicit repoint, leaving
the global record intact. Idempotent. petromall is skipped — it is not a barakat
shop. See docs/superpowers/specs/2026-07-24-uom-company-scoping-design.md
"""

import frappe
from barakat.patches._uom_scope_logic import classify_unit, scoped_name

SKIP_SITES = {"petromall.iztech.net"}

def _referencing_companies(uom):
    """Companies whose items/prices reference this uom (via Item.custom_company)."""
    rows = frappe.db.sql(
        """
        select distinct i.custom_company as c
        from `tabItem` i where i.stock_uom = %(u)s and ifnull(i.custom_company,'') != ''
        union
        select distinct i.custom_company as c
        from `tabUOM Conversion Detail` d join `tabItem` i on i.name = d.parent
        where d.uom = %(u)s and ifnull(i.custom_company,'') != ''
        union
        select distinct ip.custom_company as c
        from `tabItem Price` ip where ip.uom = %(u)s and ifnull(ip.custom_company,'') != ''
        """,
        {"u": uom}, as_dict=True,
    )
    return {r.c for r in rows if r.c}

def _used_units(company):
    rows = frappe.db.sql(
        """
        select distinct stock_uom as u from `tabItem` where custom_company=%(c)s and ifnull(stock_uom,'')!=''
        union
        select distinct d.uom as u from `tabUOM Conversion Detail` d
          join `tabItem` i on i.name=d.parent where i.custom_company=%(c)s and ifnull(d.uom,'')!=''
        union
        select distinct uom as u from `tabItem Price` where custom_company=%(c)s and ifnull(uom,'')!=''
        """,
        {"c": company}, as_dict=True,
    )
    return {r.u for r in rows if r.u}

def _repoint(company, old, new):
    n_items = frappe.db.sql("update `tabItem` set stock_uom=%(n)s where custom_company=%(c)s and stock_uom=%(o)s",
                            {"n": new, "o": old, "c": company})
    frappe.db.sql("""update `tabUOM Conversion Detail` d join `tabItem` i on i.name=d.parent
                     set d.uom=%(n)s where i.custom_company=%(c)s and d.uom=%(o)s""",
                  {"n": new, "o": old, "c": company})
    frappe.db.sql("update `tabItem Price` set uom=%(n)s where custom_company=%(c)s and uom=%(o)s",
                  {"n": new, "o": old, "c": company})

def execute():
    if frappe.local.site in SKIP_SITES:
        print(f"[barakat] scope_uom_company: skipping non-barakat site {frappe.local.site}")
        return
    companies = frappe.get_all("Company", pluck="name")
    for company in companies:
        renamed = created = repointed = skipped = 0
        for uom in _used_units(company):
            owner = frappe.db.get_value("UOM", uom, "custom_company")
            refs = _referencing_companies(uom)
            action = classify_unit(uom, owner, refs, company)
            new = scoped_name(uom, company)
            if action == "skip":
                skipped += 1
            elif action == "rename":
                frappe.rename_doc("UOM", uom, new, force=True, merge=False)
                frappe.db.set_value("UOM", new, "custom_company", company, update_modified=False)
                renamed += 1
            else:  # copy
                if not frappe.db.exists("UOM", new):
                    src = frappe.get_doc("UOM", uom)
                    frappe.get_doc({"doctype": "UOM", "uom_name": new,
                                    "enabled": src.enabled, "must_be_whole_number": src.must_be_whole_number,
                                    "custom_company": company}).insert(ignore_permissions=True)
                    created += 1
                _repoint(company, uom, new)
                repointed += 1
        frappe.db.commit()
        # Post-condition: nothing left unscoped for this company.
        leftover = frappe.db.sql(
            """select count(*) from `tabItem` where custom_company=%(c)s and stock_uom not like %(p)s""",
            {"c": company, "p": f"%{ ' - ' }{company}"})[0][0]
        print(f"[barakat] UOM scope {company}: renamed={renamed} created={created} "
              f"repointed={repointed} skipped={skipped} leftover_items={leftover}")
```

- [ ] **Step 6: Register the patch**

Append to `barakat/patches.txt`:

```
barakat.patches.scope_uom_company
```

- [ ] **Step 7: Verify on a copy of a real site (manual gate)**

```bash
# On the test EC2, against a throwaway copy or the test site:
sudo -u frappe bench --site <test-site> migrate
```
Expected: prints `[barakat] UOM scope <Company>: ... leftover_items=0` for each company; a second `bench migrate` prints `renamed=0 created=0 repointed=0`.

- [ ] **Step 8: Commit**

```bash
git add barakat/patches/scope_uom_company.py barakat/patches/_uom_scope_logic.py \
        barakat/test_uom_scope_logic.py barakat/patches.txt
git commit -m "feat(uom): data-driven per-company UOM scoping patch"
```

---

### Task 3: proxy — scoped-name + ownership for UOM

**Files:**
- Modify: `src/lib/owned.ts` (add UOM to the custom_company mechanism doc/usage)
- Modify: `src/lib/scoped-name.ts` (nothing structural — already generic; confirm)
- Test: `src/lib/owned.spec.ts` (add a UOM case)

**Interfaces:**
- Consumes: UOM records now carrying `custom_company`.
- Produces: `assertOwned(erp,'UOM',name,company,'custom_company',msg)` usable by UOM routes.

- [ ] **Step 1: Write the failing test**

Add to `src/lib/owned.spec.ts`:

```ts
test('UOM ownership via custom_company', () => {
  expect(isOwned('custom_company', { custom_company: 'Beit Al-Moneh' }, 'Kg - Beit Al-Moneh', 'Beit Al-Moneh')).toBe(true)
  expect(isOwned('custom_company', { custom_company: 'Other' }, 'Kg - Other', 'Beit Al-Moneh')).toBe(false)
  expect(isOwned('custom_company', { custom_company: 'Other' }, 'Kg - Other', null)).toBe(true)
})
```

- [ ] **Step 2: Run to verify it passes already**

Run: `cd ~/Desktop/barakat-repos/proxy-barakat && bun test src/lib/owned.spec.ts`
Expected: PASS — `isOwned` is generic; this test documents UOM using the existing `custom_company` mechanism. (No code change needed in `owned.ts` beyond adding `UOM` to the doc-comment list of custom_company doctypes.)

- [ ] **Step 3: Document UOM in owned.ts**

In `src/lib/owned.ts`, add `UOM` to the `'custom_company'` list in the header comment (line ~8), so the doctype↔mechanism map stays accurate.

- [ ] **Step 4: Run the full lib test suite**

Run: `bun test src/lib/`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lib/owned.ts src/lib/owned.spec.ts
git commit -m "feat(uom): scope UOM ownership via custom_company"
```

---

### Task 4: proxy — filter, create, and resolve UOMs per company

**Files:**
- Modify: `src/modules/products/service.ts` (`fetchSelectableUoms`, `createUOM`, `resolveUomName`)
- Test: `src/modules/products/uom.spec.ts` (create)

**Interfaces:**
- Consumes: scoped UOM records; `makeScopedName`/`stripCompanySuffix` (existing).
- Produces: `listUOMs(company)` returns only the company's units (display stripped); `createUOM(name, company)` writes suffixed + `custom_company`; `resolveUomName(uom, company)` returns the scoped name.

- [ ] **Step 1: Write the failing test**

Create `src/modules/products/uom.spec.ts` with a fake `erp` capturing the list filter and the create payload:

```ts
import { describe, test, expect } from 'bun:test'
import { createUOM, listUOMs } from './service'

function fakeErp(rows: any[]) {
  const calls: any = { listFilters: null, created: null }
  return {
    calls,
    list: async (_dt: string, opts: any) => { calls.listFilters = opts.filters; return { data: rows } },
    create: async (_dt: string, doc: any) => { calls.created = doc; return { data: { name: doc.uom_name } } },
  } as any
}

describe('UOM scoping (proxy)', () => {
  test('listUOMs filters by custom_company and strips display', async () => {
    const erp = fakeErp([{ name: 'Kg - Beit Al-Moneh', must_be_whole_number: 0 }])
    const res = await listUOMs(erp, 'Beit Al-Moneh')
    expect(JSON.parse(erp.calls.listFilters)).toEqual(
      expect.arrayContaining([['custom_company', '=', 'Beit Al-Moneh'], ['enabled', '=', 1]]))
    expect(res.data[0]).toEqual({ name: 'Kg - Beit Al-Moneh', displayName: 'Kg' })
  })

  test('createUOM writes suffixed name + custom_company', async () => {
    const erp = fakeErp([])
    const res = await createUOM(erp, 'علبة', 'Beit Al-Moneh')
    expect(erp.calls.created).toEqual({ uom_name: 'علبة - Beit Al-Moneh', custom_company: 'Beit Al-Moneh' })
    expect(res.displayName).toBe('علبة')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `bun test src/modules/products/uom.spec.ts`
Expected: FAIL — `listUOMs` filter lacks `custom_company`; `createUOM` payload lacks suffix + `custom_company`.

- [ ] **Step 3: Implement**

In `src/modules/products/service.ts`:

`fetchSelectableUoms` — replace the filter and the stale comment:

```ts
// Company-scoped: each company owns its own units (custom_company + " - <Company>"
// suffix). Safe to filter now that the scope_uom_company migration re-pointed every
// item onto its company's units. See specs/2026-07-24-uom-company-scoping-design.md
async function fetchSelectableUoms(erp: ERPNextClient, company: string): Promise<ERPUOM[]> {
  const res = await erp.list<ERPUOM>('UOM', {
    fields: JSON.stringify(['name', 'must_be_whole_number']),
    filters: JSON.stringify([['custom_company', '=', company], ['enabled', '=', 1]]),
    limit_page_length: 0,
    order_by: 'name asc',
  }).catch(() => ({ data: [] as ERPUOM[] }))
  return res.data
}
```

`createUOM` — suffix + stamp:

```ts
export async function createUOM(erp: ERPNextClient, displayName: string, company: string) {
  assertNameWithinErpLimit(displayName)
  const res = await erp.create<ERPUOM & { uom_name: string }>('UOM', {
    uom_name: makeScopedName(displayName, company),
    custom_company: company,
  })
  return { name: res.data.name, displayName: stripCompanySuffix(res.data.name, company) }
}
```

`resolveUomName` (line ~178) — change fallback `'bare'` → `'suffixed'`:

```ts
return resolveScopedName(erp, 'UOM', uom, company, 'suffixed')
```

(Import `makeScopedName` from `../../lib/scoped-name` if not already imported.)

- [ ] **Step 4: Run to verify it passes**

Run: `bun test src/modules/products/uom.spec.ts && bun run typecheck`
Expected: PASS + clean typecheck

- [ ] **Step 5: Commit**

```bash
git add src/modules/products/service.ts src/modules/products/uom.spec.ts
git commit -m "feat(uom): company-scope the proxy UOM picker, create, and resolve"
```

---

### Task 5: electrobun — suffix-tolerant `isScaleKgUom`

**Files:**
- Create: `src/shared/uom.ts`
- Create: `src/shared/uom.test.ts`
- Modify: `src/mainview/features/register/lib/register-mappers.ts` (replace the brittle kg check)

**Interfaces:**
- Produces: `isScaleKgUom(uom: string): boolean` — the single source of truth for "is this the weighable kg unit", tolerant of case and the ` - <company>` suffix.

- [ ] **Step 1: Write the failing test**

Create `src/shared/uom.test.ts`:

```ts
import { describe, expect, test } from 'bun:test'
import { isScaleKgUom } from './uom'

describe('isScaleKgUom', () => {
  test.each(['Kg', 'KG', 'kg', 'Kilogram', 'kilogram', 'Kg - Beit Al-Moneh', 'Kilogram - X'])(
    'true for %s', (u) => expect(isScaleKgUom(u)).toBe(true))
  test.each(['Bag', 'Unit', 'Piece', 'Bagkg', '', 'Kgm'])(
    'false for %s', (u) => expect(isScaleKgUom(u)).toBe(false))
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Desktop/electrobun-pos && bun test src/shared/uom.test.ts`
Expected: FAIL — `Cannot find module './uom'`

- [ ] **Step 3: Implement**

Create `src/shared/uom.ts`:

```ts
// The weighable "kilogram" unit, tolerant of case and the company scope suffix
// (" - <Company>") added by UOM company-scoping. A weighed (scale) sale is a
// fractional quantity in this unit. Piece 2 will replace this heuristic with the
// branch-configured balance UOM; until then, this recognises every kg spelling.
export function isScaleKgUom(uom: string): boolean {
  const base = uom.split(' - ')[0]!.trim().toLowerCase()
  return base === 'kg' || base === 'kilogram'
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `bun test src/shared/uom.test.ts`
Expected: PASS

- [ ] **Step 5: Wire it into the scale mapping**

In `src/mainview/features/register/lib/register-mappers.ts`, import `isScaleKgUom` from `../../../../shared/uom` and replace any direct `uom === 'Kg'` / `stockUom === 'Kg'` comparison in the weighed-line path with `isScaleKgUom(...)`. Run `grep -rn "=== \"Kg\"\|=== 'Kg'" src` first and replace each hit in the scale/weighed path.

- [ ] **Step 6: Run the register + scale suites**

Run: `bun test src/mainview/features/register src/bun/sync/push-orders-scale.spec.ts`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/shared/uom.ts src/shared/uom.test.ts src/mainview/features/register/lib/register-mappers.ts
git commit -m "feat(scale): suffix- and case-tolerant kg UOM detection"
```

---

### Task 6: electrobun — pull-uoms includes the company's scoped units

**Files:**
- Modify: `src/bun/sync/pull-uoms.ts` (only if it filters in a way that drops scoped units)
- Test: `src/bun/sync/pull-uoms.spec.ts` (extend)

**Interfaces:**
- Consumes: scoped UOM records (`Kg - <company>` with `must_be_whole_number`).
- Produces: `isWholeNumberUom(site, 'Kg - <company>')` resolves correctly.

- [ ] **Step 1: Write the failing test**

Extend `src/bun/sync/pull-uoms.spec.ts` — seed a whole-number scoped unit and assert it's stored:

```ts
test('stores scoped whole-number units by their full name', async () => {
  // fake erp returns [{ name: 'Nos - Beit Al-Moneh', must_be_whole_number: 1 }]
  await pullUoms(fakeErp([{ name: 'Nos - Beit Al-Moneh' }]), SITE)
  expect(isWholeNumberUom(SITE, 'Nos - Beit Al-Moneh')).toBe(true)
  expect(isWholeNumberUom(SITE, 'Kg - Beit Al-Moneh')).toBe(false)
})
```

- [ ] **Step 2: Run to verify current behaviour**

Run: `bun test src/bun/sync/pull-uoms.spec.ts`
Expected: PASS if `pull-uoms` already fetches by `must_be_whole_number=1` without a company filter (it stores whatever names ERPNext returns — scoped names included). If it FAILS because of an unexpected filter, proceed to Step 3.

- [ ] **Step 3: Adjust only if needed**

If the pull filters UOMs by anything that excludes scoped names, widen it to fetch all enabled `must_be_whole_number=1` UOMs (names are already company-suffixed in the data). No display change — the app stores the exact `stock_uom` string items carry.

- [ ] **Step 4: Run to verify it passes**

Run: `bun test src/bun/sync/pull-uoms.spec.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bun/sync/pull-uoms.ts src/bun/sync/pull-uoms.spec.ts
git commit -m "test(uom): confirm scoped whole-number units survive pull-uoms"
```

---

## Rollout (after all tasks merged)

1. **barakat → test**, `bench migrate` each test site (skip petromall auto), read `leftover_items=0` per company.
2. **proxy → test**, verify AP UOM picker shows only the company's units on a test shop.
3. **electrobun** build/release test channel; scan a weighed item, confirm the line still adds.
4. Repeat 1–3 to **main/prod**, per-site gate: `leftover_items=0` and picker shows only that company's units.

## Self-Review

- **Spec coverage:** data model (Task 1) ✓; migration incl. rename/copy/repoint/petromall/counts/post-condition (Task 2) ✓; proxy owned/filter/create/resolve (Tasks 3–4) ✓; electrobun isScaleKgUom + pull-uoms (Tasks 5–6) ✓; rollout ✓.
- **Placeholders:** none — every code step carries real code. Task 5 Step 5 and Task 6 Step 3 are conditional edits gated on a `grep`/test result, with the exact action specified.
- **Type consistency:** `scoped_name`/`classify_unit` (barakat) and `isScaleKgUom` (electrobun) names are used identically where referenced; proxy uses existing `makeScopedName`/`stripCompanySuffix`/`resolveScopedName`.
