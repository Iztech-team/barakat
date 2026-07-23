# Close the customer cross-company leak (work-chunk B)

**Date:** 2026-07-23
**Repo:** `barakat` (Frappe app) — fixtures + one patch. No proxy/AP/POS changes.
**Answers:** IzTechValley report 2026-07-21 — Blocker 1 (partially) and HIGH 5 (§2b)

## What we disproved first

The report's Blocker 1 said server-side company scoping "was not attempted" and that
"nothing in any of the three repos ever creates a company-scoped User Permission."
Both statements are true of the three barakat repos and both are **misleading**, because
the mechanism lives in ERPNext core, which the audit did not read:

- `erpnext/setup/doctype/employee/employee.py::update_user_permissions()` calls
  `add_user_permission("Company", …)` whenever an Employee's `user_id` changes and the
  `create_user_permission` checkbox is set (default 1).
- Frappe then enforces it generically in `frappe/model/db_query.py::add_user_permissions()`:
  it walks **every `Link` field on the doctype** (custom fields included), and where the
  field's `options` matches a doctype the user is restricted on, it appends
  `WHERE <field> IN (<permitted values>)`.

Measured on `bom.iztech.net` as a staff user created through the AP during this session
(`staffbom2@gmail.com`, company `BOM2`):

| Doctype | rows on site | visible to that user | |
|---|---|---|---|
| Sales Invoice | 94 | 0 | enforced |
| POS Invoice | 190 | 0 | enforced |
| Payment Entry | 6 | 0 | enforced |
| POS Profile | 4 | 0 | enforced |
| Item | 19,192 | 1 | enforced |
| **Customer** | **2,326** | **2,326** | **NOT enforced** |

So the tenant boundary exists, is automatic, and works. **Customer is the sole exception.**

## Root cause

Enforcement requires a `Link` field. The company markers barakat added are `Link → Company`
on Item, Item Group, Supplier, Mode of Payment, Currency Exchange, Price List and
`Branch.custom_pos_company` — but on Customer they were created as `Data`:

| Doctype | fieldname | fieldtype | options | filters? |
|---|---|---|---|---|
| Item | `custom_company` | Link | Company | yes |
| Price List | `custom_company` | Link | Company | yes |
| **Customer** | **`custom_company`** | **Data** | — | **no** |
| **Customer** | **`custom_branch`** | **Data** | — | **no** |

A `Data` field is not returned by `meta.get_link_fields()`, so Frappe never considers it.
ERPNext is not ignoring the field — it cannot see it.

**Blast radius today:** through the AP the leak is masked, because the proxy filters customers
itself (`customers/service.ts` pushes `['custom_company','=',company]`). The exposure is at the
ERPNext layer — `/app` or `/api/resource/Customer` with the user's own session returns all rows.
That is exactly the report's original complaint, and the only part of it that survives.

## Scope

Convert the two Customer markers to real links, and ship the Customer Group marker that is
missing from the repo. Nothing else — no new hook, no backfill, no `permission_query_conditions`.

### 1. `Customer.custom_company`: `Data` → `Link(Company)`

In `barakat/fixtures/custom_field.json`, set `fieldtype: "Link"`, `options: "Company"`. The
underlying column is `varchar(140)` either way, so no schema change and no data loss.

### 2. `Customer.custom_branch`: `Data` → `Link(Branch)`

Same class of latent bug (found during this review; not in the report). Only 1 of 2,326 rows
is populated. Included because leaving one `Data` marker behind re-creates the exact trap.

### 3. Ship `Customer Group.custom_company` in the fixtures

It exists on the live sites as a proper `Link → Company` (so groups are enforced today) but is
**absent from `custom_field.json`** — it was created by hand. A fresh site or a new client
install would come up without it and customer groups would silently stop being scoped. Same
drift class as the missing roles fixed in New-E.

### 4. Data migration patch

New patch in `barakat/patches/`, registered in `patches.txt`, that runs **per site** and is
defensive — it must never blank a value it does not understand:

- For `Customer.custom_company`: rows whose value matches an existing Company are left as-is.
  Rows that are empty are set to the site's default company **only when the site has exactly one
  company**; otherwise they are left and **logged**, because guessing a tenant is worse than a
  visible gap. Rows whose value matches no Company are left and logged.
- For `Customer.custom_branch`: same rule against Branch.
- The patch logs a summary (matched / blanked-and-skipped / unmatched) so the rollout can be
  evidenced, as the report asks for elsewhere.

**Why blanks matter.** In `add_user_permissions` Frappe emits
`ifnull(field,'')='' or field in (…)` unless `apply_strict_user_permissions` is on — so a row
with an **empty** company stays visible to everyone. On `bom` that is 1 customer today. Filling
blanks is therefore part of the fix, not cosmetic.

### 5. Re-assert the Company User Permission on every Employee save

The whole boundary proved above rests on one tickbox: `Employee.create_user_permission`
("User Details" section, next to User ID, default 1). ERPNext's `update_user_permissions()`:

- runs **only** when `user_id` or the checkbox itself changed — it is not re-asserted on
  ordinary saves, so a permission lost any other way is never restored; and
- **deletes** both the Employee and the **Company** permission when the box is unticked —
  while its UI description says only *"This will restrict user access to other employee
  records"*. Nothing warns that unticking also unlocks every other company's data.

New hook `reassert_company_user_permission(doc, method=None)` in
`barakat/overrides/staff_roles.py`, wired on the same `Employee` `after_insert` / `on_update`
events as `reassert_persona_roles`:

- No-op unless the Employee has a recognised persona preset **and** a linked, existing,
  non-Administrator user **and** a company — same guards as the role hook.
- Ensures a `User Permission (allow="Company", for_value=doc.company)` exists for that user.
- **Add-only. It never deletes.** A second company granted by hand to an area manager
  survives untouched — required, since staff may legitimately span shops.

This deliberately splits the two concerns the checkbox currently conflates: the checkbox keeps
owning the *own-employee-record* restriction, while barakat owns the *tenant* restriction. So
unticking it still does what its label promises, without silently dropping the company wall.

## Risks

- **Link validation on future saves.** Once the field is a `Link`, saving a Customer whose
  `custom_company` is not a real Company throws `LinkValidationError`. Mitigated by the patch
  cleaning/logging values; unmatched rows are readable but will fail on next save, which the
  patch log makes visible.
- **Apparent data loss for BOM2 users.** After the fix a BOM2 user sees ~1 customer instead of
  2,326 at the ERPNext layer. That is the intended outcome. **No AP-visible change**, because the
  proxy already filtered to the active company — so day-to-day users notice nothing.
- **Other sites' values are unverified.** Only `bom` was measured (2,324 `BOM`, 1 `BOM2`, 1 null —
  clean). The patch must be safe on sites whose values were never inspected, hence log-don't-guess.

## Testing

- **Pure/local:** a test asserting the fixture defines `Customer.custom_company` and
  `custom_branch` as `Link` with the right `options`, and that no company/branch marker anywhere
  in `custom_field.json` is `Data` — so this class of bug cannot come back (mirrors the
  `RoleFixtureCoverage` guard added for New-E).
- **On-bench:** re-run the measurement as a company-scoped user and assert
  `visible < total` for Customer, alongside the existing Item control. Green means the leak is closed.
- **Patch:** idempotent — running it twice changes nothing on the second run.
- **Company re-assert hook:** with the permission deleted, saving the Employee recreates it;
  saving again is a no-op (no duplicate row); an extra hand-granted company is still present
  afterwards; and an Employee with no preset / no login / no company is untouched.

## Out of scope

- `permission_query_conditions` / `has_permission` hooks — unnecessary; the native mechanism is
  proven to work on every other doctype.
- `apply_strict_user_permissions` (would also hide blank-marker rows). A site-wide setting with a
  much wider blast radius; revisit after this lands.
- Changing the `create_user_permission` field itself (label, default, or read-only). Re-asserting
  from our own hook achieves the goal without editing a core ERPNext field.
