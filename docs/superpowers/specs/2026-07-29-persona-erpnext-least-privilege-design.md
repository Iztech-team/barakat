# Persona ERPNext least privilege: mirror the AP matrix exactly

**Date:** 2026-07-29
**Status:** Design approved, not implemented. **Do not deploy to test or prod without the owner's explicit approval.**
**Repos touched:** `barakat` (primary), `proxy-barakat` (matrix snapshot + test only)

## Problem

The admin panel matrix in `proxy-barakat/src/modules/roles/catalog.ts` is the source of
truth for what a persona may do. Two of the three enforcement layers honour it. The third
does not.

A staff member logs into the AP with an email and password. **The same credentials work
against ERPNext directly** — `/api/resource/*` and `/app`. The proxy's route gate is not in
that path, so the only wall is the persona's ERPNext role bundle. Those bundles are built
from native ERPNext roles, which grant far more than the matrix intends.

### Measured on production, 2026-07-29

Verified by running as the user (`frappe.set_user`) on the live bench, and independently
over HTTPS with a token:

| Site | Persona | Matrix says | ERPNext actually allows |
|---|---|---|---|
| niveen1 | Cashier | `salary: none` | reads every Salary Slip — `net_pay: 2884.62` returned |
| niveen1 | Cashier | `staff: none` | reads every Employee — name, date of birth |
| bm | Cashier | `staff: none` | 138 employee records |
| bom | Cashier | `staff: none` | 166 employee records |

The full document opens, not just the name: `OPEN-DOC Salary Slip -> ALLOWED`.

**Root cause.** `PRESERVED_ROLES = {"Employee", "Employee Self Service"}` is attached to
every persona. Both grant read on `Employee` and `Salary Slip`. In stock ERPNext that is
safe, because each staff member also carries a User Permission pinning them to their own
Employee record. `barakat/overrides/staff_roles.py:158` deliberately **deletes** that User
Permission — correctly, because it scoped the user to their own record across every
doctype and hid other people's shifts and attendance from managers. Removing the leash
without removing the role left an unrestricted read.

### Two things that are NOT the problem

Both were suspected and disproven; recorded so they are not re-litigated.

1. **There is no privileged header or service account.** Every ERPNext call rides the
   logged-in user's own session cookie (`lib/erpnext.ts:262`). The one API-key path,
   `getClientForSite`, is called from nowhere. No `System Manager`, `Script Manager` or
   `Report Manager` is held by any of the 40 staff accounts across the prod sites.
2. **The tenant boundary holds.** Every persona user carries a Company User Permission and
   sees exactly one company. Cross-shop data is not reachable.

### Blocking desk access does not fix this — do not attempt it

Measured on the test box: a persona flipped to `user_type = "Website User"` still read a
full Salary Slip over REST (`net_pay: 4000`), still read Employee rows, and `/app` still
served a 175 KB desk boot payload. In Frappe, `has_desk_access()` is consulted in exactly
one place in the request path (`frappe/handler.py:195`) and only restricts upload MIME
types. `user_type` is consulted in exactly one place in the permission engine
(`frappe/permissions.py:559`), where it appends the `Desk User` pseudo-role — so the only
thing a Website User loses is DocPerms granted to that pseudo-role.

The flag does not even persist: `User.set_system_user()` recomputes `user_type` from role
`desk_access` on every save, and `reassert_persona_roles` saves the User on every Employee
save. Reproduced: `Website User` -> plain resave -> `System User`.

**Only the DocPerm layer restricts data.**

## Goals

- Every persona's ERPNext permissions equal its AP matrix row. No more, and — critically —
  no less.
- A test that fails when the two drift.
- No native ERPNext role in any persona bundle.

## Non-goals

- Changing the matrix itself. `catalog.ts` is taken as given.
- Touching owner/`Administrator` accounts, or the tenant Company User Permission.
- The `petromall` site, which is not ours.

## Design

### 1. Module to doctype map

A single map in `barakat/permissions.py`, derived from the proxy's service code and the POS
till's own calls rather than guessed:

```python
MODULE_DOCTYPES = {
  "products":   ["Item", "Item Group", "Item Price", "Product Bundle", "UOM", "Price List", "Bin"],
  "inventory":  ["Stock Entry", "Stock Reconciliation", "Stock Ledger Entry", "Bin", "Warehouse"],
  "customers":  ["Customer", "Customer Group", "Contact", "Loyalty Program",
                 "Loyalty Point Entry", "Territory"],
  "salary":     ["Salary Slip", "Salary Structure", "Salary Component",
                 "Salary Structure Assignment", "Payroll Settings"],
  "staff":      ["Employee", "Designation", "Holiday List", "Holiday List Assignment", "User"],
  "attendance": ["Attendance"],
  "finance":    ["GL Entry", "Journal Entry", "Payment Entry", "Account", "Fiscal Year"],
  "accounting": ["Account", "Mode of Payment", "Sales Taxes and Charges Template",
                 "Currency Exchange", "Price List"],
  "suppliers":  ["Supplier", "Supplier Group", "Purchase Invoice", "Payment Entry", "GL Entry"],
  "pos":        ["POS Invoice", "POS Opening Entry", "POS Closing Entry", "POS Profile",
                 "POS Employee Branch", "Device", "POS Scale Settings"],
  "warehouses": ["Warehouse"],
  "branches":   ["Branch"],
  "settings":   ["Company", "Global Defaults", "System Settings", "POS Scale Settings"],
  # one entry per module key in catalog.ts
}
```

The excerpt above is illustrative. The complete map — every module key, including the seven
`reports.*` sub-keys — is produced during implementation by the derivation in
"Proving nothing is missing / A", and reviewed alongside the removal table before anything
ships.

### 2. Generated roles

Two roles minted per module: `Barakat <Module> Reader` (read + select) and
`Barakat <Module> Writer` (read + select + write + create + delete). Roughly 30 roles.

A persona's bundle is then a literal transcription of its matrix row — `products: 'write'`
becomes `Barakat Products Writer`, `products: 'read'` becomes `Barakat Products Reader`,
`products: 'none'` grants nothing. **No native ERPNext role appears in any bundle.**

Roles are provisioned by `_provision_barakat_roles` and permed by `_grant_barakat_role_perms`
on `after_migrate`, as the existing custom roles already are. Every generated role carries
`desk_access = 0` — not as a security measure (proven above that it is not one) but so the
desk does not advertise modules the persona cannot use.

### 3. Self-service replaces the Employee roles

`PRESERVED_ROLES` drops `Employee` and `Employee Self Service`. A new `Barakat Self Service`
role carries read on `Employee` and `Salary Slip`, scoped by a `permission_query_conditions`
hook to rows belonging to the caller:

```python
# hooks.py
permission_query_conditions = {
    "Employee": "barakat.permissions.employee_self_query",
    "Salary Slip": "barakat.permissions.salary_slip_self_query",
}
has_permission = {
    "Employee": "barakat.permissions.employee_self_has_permission",
    "Salary Slip": "barakat.permissions.salary_slip_self_has_permission",
}
```

Both hooks are required. `permission_query_conditions` filters lists; `has_permission`
gates opening a single document. Shipping only the first leaves direct document reads open
— exactly the list-versus-open distinction that made the Account finding a false alarm and
the Salary Slip finding real.

Every persona keeps `Barakat Self Service`, so my-profile and my-payslip continue to work.
Personas that legitimately need cross-staff reads (HR, Manager) get `Barakat Staff Reader`
or `Barakat Salary Writer` from the matrix instead, and those are unscoped by design.

**A `permission_query_conditions` hook applies to every user of that doctype, not only to
the persona it was written for.** Registered naively, it would scope HR and Manager to their
own record too and break payroll outright — the exact class of "forgot a permission someone
needs" failure this design is meant to avoid. Both hooks must therefore return no condition
(and `has_permission` must return `None`, deferring to the normal engine) whenever the
caller holds an unscoped role for that doctype:

```python
UNSCOPED_BY_DOCTYPE = {
    "Employee":    {"Barakat Staff Reader", "Barakat Staff Writer"},
    "Salary Slip": {"Barakat Salary Reader", "Barakat Salary Writer"},
}
```

Guarded by `test_self_service_scope.py`, which asserts the Cashier is scoped **and** that HR
and Manager still read every employee and every slip.

**ERPNext re-attaches `Employee` when an Employee record links to a User.** The existing
`reassert_persona_roles` hook already rewrites the roles child table to exactly the bundle
on every Employee save, so the re-attached role is stripped on the next save. This must be
covered by a test, because the window between the two is a real read.

### 4. Keeping the two repos in sync

The matrix lives in TypeScript, the bundles in Python. A hand-copy is exactly the drift
that broke this file once before (a bundle snapshotted from pos2 named two site-local roles
and staff creation failed outright everywhere else).

One `persona-matrix.json`, committed identically in both repos. Each repo has a test
asserting its own in-code matrix matches its copy of the file. Forgetting to copy it turns
a test red in whichever repo lagged, rather than silently diverging in production.

## Proving nothing is missing

This is the part that decides whether the change is safe. A too-narrow bundle does not
throw a visible error — the AP renders an empty dropdown and the POS till silently falls
back to defaults. Five independent methods, because no single one is sufficient.

### A. Static derivation

Done, and the input to `MODULE_DOCTYPES`. Every `erp.list/get/create/update/delete/submit`
call in the proxy's 24 module directories, plus every `/api/resource/` path in the POS till.

Whitelisted method calls do not name a doctype and must be checked by hand against what
each method touches internally:

- proxy: `barakat.api.session_role.{get_my_companies, get_my_pos_branches,
  update_my_profile_name}`, `erpnext.setup.utils.get_exchange_rate`,
  `frappe.client.{cancel, get_count, insert, rename_doc}`
- till: `barakat.api.device.{check_device_profile, get_available_profiles, register_device,
  select_profile}`, `barakat.api.shift.{get_shift_invoices, get_shift_orders,
  get_shift_summary}`, `barakat.api.session_role.get_my_pos_role`,
  `frappe.client.{get_count, get_value}`

### B. Removal diff — the primary safety net

Before any bundle change ships, a script computes for each persona the **current** effective
set of `(doctype, permission)` pairs and the **proposed** set, and reports the difference.

Every removal must appear in the spec's removal table with a justification. Nothing is
removed because it happened not to be in `MODULE_DOCTYPES`. If a permission is being taken
away and no one can say which matrix cell forbids it, it stays until that is resolved.

This inverts the risk: instead of hoping the new list is complete, we prove that everything
dropped was dropped on purpose.

### C. Preserve `select` where read is denied

Frappe's list query permits `select` **or** `read`. Link-field pickers rely on `select`
alone. Measured on niveen1: a cashier has `has_permission("Account", "read") == False` yet
lists 98 account names, because `Employee Self Service` grants `select` on Account. Opening
one is denied and extra fields come back empty.

That behaviour is correct and must survive. Every generated Reader role grants `select`
alongside `read`, and any doctype appearing as a Link target on a form the persona can open
gets `select` even where `read` is not granted. Losing this empties dropdowns with no error
— the failure mode that already cost two rounds of fixes.

### D. Child tables are false positives

Child doctypes (`istable = 1`, e.g. `POS Employee Branch`) inherit the parent's permissions
and show as "nobody has read". They are excluded from the missing-permission analysis.

### E. Runtime verification per persona

An API sweep proves reachability but cannot see an empty dropdown. For each of the six
personas, on the test site:

1. Walk every AP form the persona can open; confirm every picker populates.
2. Run a full POS shift on a Manager device session and a Branch Supervisor device session:
   open, sell a weighed item and a bundle, redeem loyalty, close.
3. Confirm my-profile and my-payslip render, and show only that user's own records.

## Guard tests

| Test | Asserts |
|---|---|
| `test_persona_matches_matrix.py` | For each persona × doctype in `MODULE_DOCTYPES`, the union of DocPerms from the bundle equals what the matrix permits — no more, no less |
| `test_pos_till_perms.py` | Manager and Branch Supervisor keep read on all 22 till doctypes and write on the six the till writes |
| `test_self_service_scope.py` | A Cashier sees exactly one Employee row and only their own Salary Slips, by list **and** by direct document open |
| `test_matrix_snapshot.py` (both repos) | The in-code matrix matches `persona-matrix.json` |
| existing `test_persona_guard.py` | No forbidden role in any bundle — unchanged |

## Rollout

1. `_backfill_persona_roles` is wired into `after_migrate`, so a bench pull brings every
   persona user on that site onto the new bundle. It writes a per-user before/after line to
   the Error Log — the only way to see afterwards what a migrate actually changed.
2. Ship to `test`. Run the full runtime verification above.
3. **Stop. Owner approval required before prod.**
4. Prod: migrate each site individually, excluding `petromall`. Re-run the removal diff on
   each site first — sites differ in which roles they define.

Rollback is `git revert` plus a re-run of the backfill, which is idempotent and reads the
bundle fresh.

## Risks

- **Manager is the sharp edge.** It is both the shop's day-to-day administrator and the POS
  till's device account. One missing doctype breaks tills quietly. Verified against a real
  shift on test before prod, not by API sweep.
- **Sites define different roles.** The bundle is already intersected with the site's actual
  Role table; the generated roles are minted by the app, so this risk shrinks, but the
  removal diff still runs per site.
- **The permlevel-1 trap.** `User.roles` is writable only by System Manager; anything
  assigning roles must rewrite the child table and save with `ignore_permissions=True`. The
  existing hook already does this. Do not refactor it into `add_roles`.
- **Auto-backfill on migrate is subtractive.** This was deliberately manual before. The
  Error Log diff and the removal table are what make it acceptable.

## Open questions

None blocking. The removal table in step B is produced during implementation and reviewed
before anything ships.
