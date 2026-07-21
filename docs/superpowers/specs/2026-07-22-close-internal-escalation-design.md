# Close internal privilege-escalation paths (work-chunk A)

**Date:** 2026-07-22
**Repo:** `barakat` (Frappe app) — backend only, no proxy/AP changes
**Answers:** IzTechValley verification report (2026-07-21) — Blocker 2 and New-item A

## Problem

Two related escalation paths the report found:

- **Blocker 2 — persona preset is caller-controlled.** `Employee.custom_role_preset` is a
  plain `Link` with no permlevel and no read-only. Anyone who can write an Employee can set
  or change the stamp, and the `reassert_persona_roles` hook then grants that persona's whole
  role bundle with `ignore_permissions=True`. So a limited HR user could stamp someone
  "Accountant" and hand out GL/payables write that HR does not otherwise have.
- **New-item A — Staff Manager holds User + User Permission CRUD.** `Barakat Staff Manager`
  grants `User (create/write)` and `User Permission (read/write/create/delete)` at permlevel 0,
  and the role is in **both** the Manager and HR bundles. So HR can mint users and delete the
  very User Permission rows the code calls the tenant boundary.

### Verified facts this design relies on

- The proxy staff flow (`proxy-barakat/src/modules/staff/service.ts`) creates staff by **direct
  doctype writes under the caller's own ERPNext session** — `create('User')` (`:410`),
  `create('Employee')` with `custom_role_preset` (`:724`). There is no service account.
- **Nothing** in barakat, proxy, or AP ever *creates* a `User Permission`. The only User
  Permission write anywhere is the barakat hook *deleting* the own-record one (`staff_roles.py:88-91`,
  elevated). Grep of proxy `src/` and AP `src/` for "User Permission" → no matches.
  → The `User Permission` grant on `Barakat Staff Manager` is **entirely unused by onboarding.**
- `Barakat Staff Manager` is in the Manager bundle (`permissions.py:232`) and the HR bundle
  (`permissions.py:299`). Native `HR Manager`/`HR User` (kept in the HR bundle) cover payroll —
  Employee, Attendance, Salary Slip, Salary Structure Assignment — independently of Staff Manager.

## Decision (business rule from the owner)

Adding/editing staff is a **Manager** job. HR should no longer do it (before, both could).
**HR keeps payroll** (salaries, attendance, pay slips); HR loses the ability to create login
accounts and to assign/change a staff member's role stamp.

A Manager may assign **any** preset. This is delegation, not escalation: the Manager bundle is
already near-full-admin (Accounts/Sales/Stock/Purchase/HR managers), so stamping someone
"Accountant" grants no power the Manager lacks. The escalation the report worried about was a
*limited* role handing out a *bigger* stamp — closed by gating the stamp to staff-admins and
removing HR's staff-admin key.

## Changes (all in the `barakat` app)

### 1. Gate the role stamp — new `validate` guard on Employee

New function `guard_role_preset` in `barakat/overrides/staff_roles.py`, wired on
`doc_events["Employee"]["validate"]` in `hooks.py`. Logic:

- If the new `custom_role_preset` is empty → allow (clearing/leaving blank is always fine).
- If `custom_role_preset` is **unchanged** on this save → allow (so HR saving an employee for a
  salary/attendance edit is never blocked — the guard only fires when the stamp itself changes).
- Bypass for system flows: `frappe.session.user == "Administrator"`, or `frappe.flags.in_install`,
  or `frappe.flags.in_migrate` (backfill / patches run elevated).
- Otherwise the caller must hold a **staff-admin** role to set/change the stamp:
  `Barakat Staff Manager` (Managers do; HR no longer will) **or** `System Manager` (owner/setup).
  If not → `frappe.throw(_("Only a manager can assign or change a staff member's role."), frappe.PermissionError)`.

This is the real lock: it fires under the *caller's* session (not the elevated reassert path), so it
holds even though native `HR Manager` still has generic Employee write. `permlevel` alone would not
work — the reassert hook uses `ignore_permissions`.

### 2. Remove `Barakat Staff Manager` from the HR bundle

In `permissions.py`, drop `"Barakat Staff Manager"` from the `"HR"` tuple in `PERSONA_ROLE_BUNDLES`.
HR retains `HR Manager`, `HR User`, `Barakat Attendance Manager`, `Barakat Commerce Reader`,
`Barakat Reference Reader` → payroll/attendance intact; login-user creation gone.

### 3. Strip the unused `User Permission` grant from Staff Manager

In `install.py`, remove `"User Permission": (...)` from `STAFF_MANAGER_PERMS`. It is unused by
onboarding (verified above) and its removal closes "HR/Manager can delete the tenant-boundary rows."
Keep `User`, `Employee`, `Designation`, `Salary Structure Assignment` — the staff-create flow needs them.

### 4. Migration patch (so the fix reaches already-provisioned sites)

`add_permission` only *adds*; dropping entries from `STAFF_MANAGER_PERMS` will not remove the
Custom DocPerm already written on live sites. New patch in `barakat/patches/` (registered in
`patches.txt`) that:

- Deletes the Custom DocPerm row granting `Barakat Staff Manager` on `User Permission` (idempotent:
  no-op if absent).
- Leaves role-bundle re-normalization to the existing backfill (see Rollout) — the patch does not
  call the subtractive backfill itself, matching the current "backfill is a manual step" model.

## Rollout

1. Push to `dev`, then promote per the normal merge flow when ready.
2. On each live site: `git -C apps/barakat pull upstream <branch>` → `bench --site <site> migrate`
   (runs the patch) → `bench restart`.
3. **Re-run the persona backfill on each live site** so existing HR users lose Staff Manager and
   get re-normalized to the updated HR bundle:
   `bench --site <site> execute barakat.setup.install.backfill_persona_roles`.
   (Backfill has been run before on all live sites; this re-applies it against the new bundles.)
   Record the command output and any Employees skipped for a missing/unknown preset.

## Testing

- **Guard — reject:** a caller holding only the HR bundle (no Staff Manager) setting/changing
  `custom_role_preset` → `PermissionError`.
- **Guard — allow (Manager):** a caller holding `Barakat Staff Manager` sets any preset → succeeds.
- **Guard — allow (unchanged):** an HR caller saves an Employee **without** touching the preset
  (e.g. a salary/attendance edit) → succeeds (guard must not fire).
- **Guard — allow (system):** backfill / migrate path (`in_migrate`/Administrator) rewriting an
  Employee → succeeds.
- **HR bundle:** `bundle_for("HR")` no longer contains `Barakat Staff Manager`; still contains the
  HR/payroll roles. The import-time `FORBIDDEN_ROLES` assertion still passes.
- **Staff Manager perms:** after migrate + patch, `Barakat Staff Manager` has **no** DocPerm on
  `User Permission`, and still has its `User`/`Employee`/`Designation`/`SSA` perms.
- **Payroll intact (verify during impl):** an HR user can still create + submit a Salary Structure
  Assignment and read Salary Slips after Staff Manager is removed (native `HR Manager` must cover it).

## Out of scope (explicitly)

- Hiding the AP staff page from the HR persona (UX layer). Backend enforcement above is what
  prevents the escalation; the AP nicety is a small follow-up.
- Replacing the direct-write onboarding with a whitelisted service method (would let us also drop
  `User: create` from the role). Unnecessary once the stamp is gated; revisit only if zero-direct-write
  is ever a goal.
- Blocker 1 (server-side company scoping) and HIGH 4/5 — separate work-chunks (B, C).
