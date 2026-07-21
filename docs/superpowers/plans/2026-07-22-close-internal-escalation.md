# Close Internal Escalation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a limited persona (HR) from handing out privileged role stamps, and remove an unused master-permission — closing Blocker 2 and New-item A from the IzTechValley 2026-07-21 report.

**Architecture:** Three backend changes in the `barakat` Frappe app: (1) a pure decision predicate + a `validate` hook that gates `Employee.custom_role_preset` behind a staff-admin role; (2) remove `Barakat Staff Manager` from the HR persona bundle; (3) remove the unused `User Permission` grant from that role, plus a migration patch so the change reaches already-provisioned sites. The security-critical decision lives in the Frappe-free `permissions.py` so it is unit-testable locally.

**Tech Stack:** Python 3, Frappe framework, `unittest` (+ `frappe.tests.utils.FrappeTestCase` for on-bench tests).

## Global Constraints

- **Backend only.** No changes to `proxy-barakat` or `admin_panel_barakat`. (Verified: the proxy creates staff by direct doctype writes under the caller's session; nothing anywhere creates a `User Permission`.)
- **Branch:** work on `dev`, commit per task. (Promotion to test/main is a later, separate step.)
- **Role name string, verbatim:** `"Barakat Staff Manager"`. System super-role: `"System Manager"`.
- **Error copy, verbatim:** `Only a manager can assign or change a staff member's role.`
- **Test environment reality:** this is a Windows dev box with no Frappe install — `import frappe` fails locally. **Pure tests** (importing only `barakat.permissions`, which has zero imports) run locally with `python -m unittest`. **Frappe tests** (importing `frappe`/`staff_roles`) run only on a bench via `bench run-tests` on the test server (`ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35`). Run local Python as `/c/Python313/python` from the repo root `C:/Users/IzTech-OTbaileh/Desktop/bar/barakat`.
- **HR keeps payroll:** HR retains native `HR Manager`/`HR User` (salary, attendance, slips). Only the staff-admin capability is removed.

---

### Task 1: Preset-assignment predicate + role constant (pure, local TDD)

**Files:**
- Modify: `barakat/permissions.py` (add constant + predicate near the end, after `bundle_for`)
- Create: `barakat/overrides/test_persona_guard.py`

**Interfaces:**
- Produces: `STAFF_MANAGER_ROLE: str` (= `"Barakat Staff Manager"`); `PRESET_ASSIGN_ROLES: frozenset[str]`; `may_assign_preset(caller_roles, *, is_administrator=False, is_system_context=False) -> bool`. Consumed by Task 3 (`staff_roles.guard_role_preset`) and Task 4 (`install.py`).

- [ ] **Step 1: Write the failing test**

Create `barakat/overrides/test_persona_guard.py`:

```python
"""Pure, Frappe-free tests for the persona-preset guard decision and the persona
bundles. Runs locally (`python -m unittest barakat.overrides.test_persona_guard`)
and under the bench test runner — it imports only `barakat.permissions`, which has
no Frappe dependency.
"""

import unittest

from barakat.permissions import STAFF_MANAGER_ROLE, may_assign_preset


class MayAssignPreset(unittest.TestCase):
    def test_staff_admin_may_assign(self):
        self.assertTrue(may_assign_preset([STAFF_MANAGER_ROLE, "HR User"]))

    def test_system_manager_may_assign(self):
        self.assertTrue(may_assign_preset(["System Manager"]))

    def test_plain_caller_may_not_assign(self):
        self.assertFalse(may_assign_preset(["HR Manager", "HR User"]))

    def test_no_roles_may_not_assign(self):
        self.assertFalse(may_assign_preset([]))

    def test_administrator_bypasses(self):
        self.assertTrue(may_assign_preset([], is_administrator=True))

    def test_system_context_bypasses(self):
        self.assertTrue(may_assign_preset([], is_system_context=True))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python313/python -m unittest barakat.overrides.test_persona_guard -v`
Expected: FAIL — `ImportError: cannot import name 'STAFF_MANAGER_ROLE'` (and `may_assign_preset`).

- [ ] **Step 3: Write minimal implementation**

In `barakat/permissions.py`, after the `bundle_for` function (end of file), append:

```python


# ── Who may assign an Employee's persona preset ──────────────────────────────
# `Employee.custom_role_preset` drives the whole role bundle a user receives, so
# setting or changing it is a privileged act. Only a staff-admin (the Manager
# persona, via STAFF_MANAGER_ROLE) or the owner/System Manager may do it — a
# limited persona such as HR must not be able to stamp someone "Accountant" and
# hand out GL write it does not otherwise have.
STAFF_MANAGER_ROLE = "Barakat Staff Manager"

# Roles whose holder may set/change a persona preset. Administrator bypasses
# separately (see `may_assign_preset`).
PRESET_ASSIGN_ROLES = frozenset({STAFF_MANAGER_ROLE, "System Manager"})


def may_assign_preset(caller_roles, *, is_administrator=False, is_system_context=False):
    """Whether a caller may set or change an Employee's `custom_role_preset`.

    Pure decision — no Frappe. `caller_roles` is any iterable of role names.
    `is_administrator` covers the Administrator account; `is_system_context`
    covers install / migrate / patch flows that run elevated.
    """
    if is_administrator or is_system_context:
        return True
    return not PRESET_ASSIGN_ROLES.isdisjoint(set(caller_roles))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python313/python -m unittest barakat.overrides.test_persona_guard -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add barakat/permissions.py barakat/overrides/test_persona_guard.py
git commit -m "feat(perms): add may_assign_preset predicate + staff-admin role constant"
```

---

### Task 2: Remove Barakat Staff Manager from the HR bundle (pure, local TDD)

**Files:**
- Modify: `barakat/permissions.py:296-303` (the `"HR"` tuple in `PERSONA_ROLE_BUNDLES`)
- Modify: `barakat/overrides/test_persona_guard.py` (add a bundle test class)

**Interfaces:**
- Consumes: `bundle_for`, `PERSONA_ROLE_BUNDLES`, `FORBIDDEN_ROLES`, `STAFF_MANAGER_ROLE` from Task 1 / existing `permissions.py`.

- [ ] **Step 1: Write the failing test**

In `barakat/overrides/test_persona_guard.py`, update the import line and add a class before the `if __name__` guard:

Change the import to:

```python
from barakat.permissions import (
    FORBIDDEN_ROLES,
    PERSONA_ROLE_BUNDLES,
    STAFF_MANAGER_ROLE,
    bundle_for,
    may_assign_preset,
)
```

Add:

```python
class HrBundleNoLongerStaffAdmin(unittest.TestCase):
    def test_hr_has_no_staff_admin_role(self):
        self.assertNotIn(STAFF_MANAGER_ROLE, bundle_for("HR"))

    def test_hr_keeps_payroll_roles(self):
        hr = bundle_for("HR")
        self.assertIn("HR Manager", hr)
        self.assertIn("HR User", hr)

    def test_manager_still_staff_admin(self):
        self.assertIn(STAFF_MANAGER_ROLE, bundle_for("Manager"))

    def test_no_bundle_leaks_forbidden_role(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            self.assertEqual(FORBIDDEN_ROLES.intersection(roles), set(), persona)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python313/python -m unittest barakat.overrides.test_persona_guard -v`
Expected: FAIL on `test_hr_has_no_staff_admin_role` — `"Barakat Staff Manager"` is still in the HR bundle.

- [ ] **Step 3: Write minimal implementation**

In `barakat/permissions.py`, edit the `"HR"` tuple — delete the `"Barakat Staff Manager",` line and update the leading comment:

```python
	# staff READ; attendance/salary write; branches, roles, reports read. Payroll
	# only — the staff-admin role (Barakat Staff Manager) was removed 2026-07-22 so
	# only the Manager persona can create logins / assign role presets.
	"HR": (
		"HR Manager",
		"HR User",
		"Barakat Attendance Manager",
		"Barakat Commerce Reader",
		"Barakat Reference Reader",
	),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python313/python -m unittest barakat.overrides.test_persona_guard -v`
Expected: PASS (10 tests). The module-level `FORBIDDEN_ROLES` assertion in `permissions.py` also still holds (the module imports without error).

- [ ] **Step 5: Commit**

```bash
git add barakat/permissions.py barakat/overrides/test_persona_guard.py
git commit -m "feat(perms): drop Barakat Staff Manager from the HR bundle (HR keeps payroll)"
```

---

### Task 3: Guard hook wrapper + wiring (Frappe; on-bench test)

**Files:**
- Modify: `barakat/overrides/staff_roles.py` (add `_` import, extend the `barakat.permissions` import, add `guard_role_preset`)
- Modify: `barakat/hooks.py:114-119` (make `Employee.validate` a list including the guard)
- Create: `barakat/overrides/test_staff_roles.py`

**Interfaces:**
- Consumes: `may_assign_preset`, `STAFF_MANAGER_ROLE` (Task 1).
- Produces: `guard_role_preset(doc, method=None)` — reads `doc.custom_role_preset` and `doc.has_value_changed`, raises `frappe.PermissionError` when disallowed. Wired on `Employee.validate`.

- [ ] **Step 1: Write the failing test**

Create `barakat/overrides/test_staff_roles.py`:

```python
"""On-bench tests for the persona-preset guard wrapper and the staff-admin role's
permissions. Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_staff_roles
Not runnable on the Windows dev box (imports `frappe`).
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.staff_roles import guard_role_preset
from barakat.permissions import STAFF_MANAGER_ROLE


class _Doc:
    """Minimal Employee stand-in: the guard only reads these two members."""

    def __init__(self, preset, changed):
        self.custom_role_preset = preset
        self._changed = changed

    def has_value_changed(self, fieldname):
        return self._changed


@contextmanager
def _as(user, roles):
    saved = frappe.session.user
    frappe.session.user = user
    try:
        with patch("frappe.get_roles", return_value=roles):
            yield
    finally:
        frappe.session.user = saved


class GuardRolePreset(FrappeTestCase):
    def test_blocks_non_staff_admin(self):
        with _as("hr@example.com", ["HR Manager", "HR User"]):
            with self.assertRaises(frappe.PermissionError):
                guard_role_preset(_Doc("Accountant", changed=True))

    def test_allows_staff_admin(self):
        with _as("manager@example.com", [STAFF_MANAGER_ROLE]):
            guard_role_preset(_Doc("Cashier", changed=True))  # must not raise

    def test_allows_when_preset_unchanged(self):
        with _as("hr@example.com", ["HR Manager"]):
            guard_role_preset(_Doc("Accountant", changed=False))  # must not raise

    def test_allows_empty_preset(self):
        with _as("hr@example.com", ["HR Manager"]):
            guard_role_preset(_Doc("", changed=True))  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run (on the test bench): `bench --site <site> run-tests --module barakat.overrides.test_staff_roles`
Expected: FAIL — `ImportError: cannot import name 'guard_role_preset' from 'barakat.overrides.staff_roles'`.
(If deferring bench access, this task's local proxy is Task 1's predicate tests, which already cover the allow/deny decision.)

- [ ] **Step 3: Write minimal implementation**

In `barakat/overrides/staff_roles.py`, change the imports near the top:

```python
import frappe
from frappe import _

from barakat.permissions import (
    FORBIDDEN_ROLES,
    PERSONAS,
    PRESERVED_ROLES,
    STAFF_MANAGER_ROLE,
    bundle_for,
    may_assign_preset,
)
```

Add this function (place it above `reassert_persona_roles`):

```python
def guard_role_preset(doc, method=None):
    """Reject setting/changing an Employee's persona preset unless the caller may.

    Wired on Employee `validate`. The stamp drives the whole role bundle, so only a
    staff-admin (the Manager persona) or the owner/System Manager may assign it. HR,
    which no longer carries the staff-admin role, is blocked here even though native
    `HR Manager` still gives it generic Employee write for payroll. Fires only when
    the preset actually changes, so a salary/attendance edit is never affected.
    """
    new_preset = (doc.custom_role_preset or "").strip()
    if not new_preset:
        return
    if not doc.has_value_changed("custom_role_preset"):
        return
    if may_assign_preset(
        frappe.get_roles(frappe.session.user),
        is_administrator=frappe.session.user == "Administrator",
        is_system_context=bool(
            frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch
        ),
    ):
        return
    frappe.throw(
        _("Only a manager can assign or change a staff member's role."),
        frappe.PermissionError,
    )
```

In `barakat/hooks.py`, change the `Employee` block so `validate` is a list (keeps the existing pin validation, adds the guard):

```python
	"Employee": {
		"validate": [
			"barakat.validations.validate_employee_pin",
			"barakat.overrides.staff_roles.guard_role_preset",
		],
		"after_insert": "barakat.overrides.staff_roles.reassert_persona_roles",
		"on_update": "barakat.overrides.staff_roles.reassert_persona_roles",
	},
```

- [ ] **Step 4: Run test to verify it passes**

Run (on the test bench): `bench --site <site> run-tests --module barakat.overrides.test_staff_roles`
Expected: PASS (4 tests in `GuardRolePreset`).
Also re-run the local pure suite to confirm no regression:
`/c/Python313/python -m unittest barakat.overrides.test_persona_guard -v` → PASS (10).

- [ ] **Step 5: Commit**

```bash
git add barakat/overrides/staff_roles.py barakat/hooks.py barakat/overrides/test_staff_roles.py
git commit -m "feat(perms): gate custom_role_preset behind a staff-admin validate hook"
```

---

### Task 4: Remove the unused User Permission grant from Staff Manager (Frappe)

**Files:**
- Modify: `barakat/setup/install.py:361` (replace the local `STAFF_MANAGER_ROLE` literal with an import) and `install.py:365-371` (`STAFF_MANAGER_PERMS` — drop the `User Permission` entry)
- Modify: `barakat/overrides/test_staff_roles.py` (add a perms assertion class)

**Interfaces:**
- Consumes: `STAFF_MANAGER_ROLE` from `barakat.permissions` (Task 1).

- [ ] **Step 1: Write the failing test**

Append to `barakat/overrides/test_staff_roles.py`:

```python
class StaffManagerPerms(FrappeTestCase):
    def test_no_user_permission_grant(self):
        # After migrate, Barakat Staff Manager must hold no DocPerm on User Permission.
        rows = frappe.get_all(
            "Custom DocPerm",
            filters={"role": STAFF_MANAGER_ROLE, "parent": "User Permission"},
            pluck="name",
        )
        self.assertEqual(rows, [])

    def test_keeps_user_create(self):
        rows = frappe.get_all(
            "Custom DocPerm",
            filters={"role": STAFF_MANAGER_ROLE, "parent": "User"},
            pluck="name",
        )
        self.assertTrue(rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run (on the test bench, against a site that has already migrated the *old* code): `bench --site <site> run-tests --module barakat.overrides.test_staff_roles`
Expected: FAIL on `test_no_user_permission_grant` — the old grant row still exists. (This becomes green only after Step 3 **and** the Task 5 patch/migrate runs; see Task 5.)

- [ ] **Step 3: Write minimal implementation**

In `barakat/setup/install.py`, replace line 361:

```python
STAFF_MANAGER_ROLE = "Barakat Staff Manager"
```

with:

```python
from barakat.permissions import STAFF_MANAGER_ROLE
```

Then in `STAFF_MANAGER_PERMS`, delete the `User Permission` line so it reads:

```python
STAFF_MANAGER_PERMS = {
	"User": ("read", "write", "create"),
	"Employee": ("read", "write", "create"),
	"Designation": ("read", "write", "create"),
	"Salary Structure Assignment": ("read", "write", "create", "submit", "cancel"),
}
```

- [ ] **Step 4: Run test to verify it passes**

On a fresh/clean bench site (where migrate writes perms from the new dict), or after Task 5's patch runs on an existing site:
`bench --site <site> run-tests --module barakat.overrides.test_staff_roles`
Expected: PASS (`StaffManagerPerms` both tests). Local pure suite unaffected.

- [ ] **Step 5: Commit**

```bash
git add barakat/setup/install.py barakat/overrides/test_staff_roles.py
git commit -m "feat(perms): drop unused User Permission grant from Barakat Staff Manager"
```

---

### Task 5: Migration patch to revoke the grant on live sites (Frappe)

**Files:**
- Create: `barakat/patches/revoke_staff_manager_user_permission_perm.py`
- Modify: `barakat/patches.txt` (append under `[post_model_sync]`)

**Interfaces:**
- Consumes: `STAFF_MANAGER_ROLE` from `barakat.permissions`.

- [ ] **Step 1: Write the patch**

Create `barakat/patches/revoke_staff_manager_user_permission_perm.py`:

```python
"""Remove the now-unused User Permission DocPerm from `Barakat Staff Manager`.

The role was granted read/write/create/delete on User Permission for a staff-create
flow that never actually writes User Permission rows (verified across barakat, proxy
and AP). `add_permission` on migrate only ever ADDS, so dropping the grant from the
source dict does not remove it from sites already migrated — this patch does.

See docs/superpowers/specs/2026-07-22-close-internal-escalation-design.md.
"""

import frappe

from barakat.permissions import STAFF_MANAGER_ROLE


def execute():
    frappe.db.delete(
        "Custom DocPerm",
        {"role": STAFF_MANAGER_ROLE, "parent": "User Permission"},
    )
    frappe.clear_cache(doctype="User Permission")
```

- [ ] **Step 2: Register the patch**

In `barakat/patches.txt`, append a line at the end (under `[post_model_sync]`):

```
barakat.patches.revoke_staff_manager_user_permission_perm
```

- [ ] **Step 3: Run migrate + verify on the test bench**

```bash
bench --site <site> migrate
bench --site <site> run-tests --module barakat.overrides.test_staff_roles
```
Expected: migrate runs the patch once; the full `test_staff_roles` suite (guard + perms) PASSES, including `test_no_user_permission_grant` now green.

- [ ] **Step 4: Commit**

```bash
git add barakat/patches/revoke_staff_manager_user_permission_perm.py barakat/patches.txt
git commit -m "fix(perms): patch to revoke unused Staff Manager User Permission grant"
```

---

## Rollout (after all tasks, when promoting)

1. Push `dev`; promote per the normal merge flow when the user says.
2. Per live site: `sudo -u frappe git -C apps/barakat pull upstream <branch>` → `sudo -u frappe bench --site <site> migrate` (runs the patch) → `sudo -u frappe bench restart`.
3. **Re-run the persona backfill per site** so existing HR users lose Staff Manager and are re-normalized to the new HR bundle, and record the output + any skipped Employees:
   `sudo -u frappe bench --site <site> execute barakat.setup.install.backfill_persona_roles`.

## Self-review notes

- **Spec coverage:** change #1 (guard) → Tasks 1+3; #2 (HR bundle) → Task 2; #3 (drop User Permission) → Task 4; #4 (patch) → Task 5; testing matrix → tests across Tasks 1–5; rollout/backfill → Rollout section. All spec sections mapped.
- **One flagged assumption to confirm during Task 3/4 on-bench run:** HR retains Salary Structure Assignment create/submit via native `HR Manager` after Staff Manager is removed. If a payroll test shows HR can no longer assign salary, add SSA to a kept HR role rather than reverting the bundle change.
