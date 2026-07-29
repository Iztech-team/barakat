# Persona ERPNext Least Privilege Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Barakat persona's ERPNext permissions equal its admin-panel matrix row, so a cashier who logs straight into ERPNext can no longer read salaries or the staff directory.

**Architecture:** A module-to-doctype map plus a Python transcription of the AP matrix generate one `Barakat <Module> Reader` / `Barakat <Module> Writer` role per module capability. Persona bundles become mechanical lookups against that matrix, with zero native ERPNext roles. The `Employee` / `Employee Self Service` roles are replaced by a self-scoped `Barakat Self Service` backed by `permission_query_conditions` + `has_permission` hooks. A guard test fails when ERPNext and the matrix drift.

**Tech Stack:** Python 3, Frappe/ERPNext v15, `unittest` (pure, Frappe-free where possible), bench console for site verification.

## Global Constraints

- **NO DEPLOYMENT.** Do not push to `test` or `main`, do not run `bench migrate` on any test or prod site, and do not run the backfill against any site. Work lands on `dev` only. The owner approves deployment separately.
- **Never touch `petromall.iztech.net`.** It is not a Barakat site. Exclude it from every site loop.
- Work on branch `dev` in `C:/Users/IzTech-OTbaileh/Desktop/bar/barakat`. The proxy change (Task 10) is in `C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat`, also on `dev`, which has 4 unrelated uncommitted changes — do not commit those.
- `barakat/permissions.py` must stay **Frappe-free** (no `import frappe`). It is imported by pure unittests. Frappe-dependent code goes in `barakat/overrides/` or `barakat/setup/`.
- Bundles are always intersected with the roles that exist on the site before being applied (`persona_role_bundle`). Never assume a role exists.
- `User.roles` is permlevel 1. Any code assigning roles must rewrite the child table and `save(ignore_permissions=True)`. Do not refactor `reassert_persona_roles` to use `add_roles`.
- Run pure tests with `python -m unittest barakat.overrides.test_persona_guard` from the repo root.

---

## Two amendments to the spec, discovered while reading the code

Both are recorded here because they change what "mirror the matrix exactly" can mean.

### A. The POS till needs reads the matrix does not grant

The desktop till runs under a Manager **or Branch Supervisor** device session and pulls
`System Settings`, `Global Defaults`, `Device`, `POS Scale Settings`, `Company`, `Currency`,
`Branch`, `UOM`, `Sales Taxes and Charges Template`, `Pricing Rule` and `Product Bundle` at
sync time. Branch Supervisor is `settings: none` in the matrix. A strict mirror would
therefore **break every Branch Supervisor till** — silently, because the till try/catches
each pull and falls back to defaults.

This is why `Barakat POS Operator` already carries `System Settings` / `Global Defaults`
read. The plan keeps that as an explicit, documented additive set — `TILL_REQUIRED_READS` —
held only by the two personas that may log a device in. The matrix governs the admin panel;
the till's needs are additive, read-only, and asserted by their own test. The "no more than
the matrix" assertion excludes exactly this set and nothing else.

### B. `update_permission_property` never revokes

`_grant_barakat_role_perms` only ever sets a perm to `1`. If a role's perm set shrinks
between releases, the old grant survives on every already-migrated site. That is harmless
while roles only ever widen; this change narrows them, so a revoke pass is required or the
whole exercise silently no-ops on existing sites. Task 7 adds it.

---

## File Structure

| File | Responsibility |
|---|---|
| `barakat/persona_matrix.py` (create) | `MODULE_DOCTYPES`, `PERSONA_MATRIX`, `TILL_REQUIRED_READS`. Pure data, Frappe-free. |
| `barakat/persona_matrix.json` (create) | Cross-repo snapshot of `PERSONA_MATRIX`. Byte-identical copy lives in the proxy. |
| `barakat/permissions.py` (modify) | Generate module roles from the map; derive bundles from the matrix; self-service scope helpers. |
| `barakat/overrides/self_service.py` (create) | The two Frappe hooks for `Employee` and `Salary Slip`. Mirrors `overrides/gl_entry.py`. |
| `barakat/hooks.py` (modify) | Register the new hooks. |
| `barakat/setup/install.py` (modify) | Revoke stale perms; wire the backfill into `after_migrate`. |
| `barakat/overrides/test_persona_guard.py` (modify) | Extend existing pure tests. |
| `barakat/test_persona_matches_matrix.py` (create) | The guard: ERPNext perms == matrix, per persona per doctype. |
| `barakat/test_self_service_scope.py` (create) | Cashier scoped; HR and Manager NOT scoped. |
| `barakat/scripts/perm_audit.py` (create) | Read-only baseline capture + removal diff, run via bench console. |
| `proxy-barakat/src/modules/roles/persona-matrix.json` (create) | Same snapshot. |
| `proxy-barakat/src/modules/roles/matrix-snapshot.test.ts` (create) | Fails when `catalog.ts` drifts from the snapshot. |

---

## Task 1: Capture the permission baseline (read-only, do this first)

The removal diff is the primary safety net. It needs a "before" picture captured from real
sites while they still run the old bundles. Nothing here changes any site.

**Files:**
- Create: `barakat/scripts/__init__.py`
- Create: `barakat/scripts/perm_audit.py`

**Interfaces:**
- Produces: `effective_perms(persona) -> dict[str, set[str]]` mapping doctype to the set of
  permission names the persona's bundle grants on this site. Used by Task 9's diff.

- [ ] **Step 1: Create the package marker**

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/bar/barakat
mkdir -p barakat/scripts && printf '' > barakat/scripts/__init__.py
```

- [ ] **Step 2: Write the audit script**

Create `barakat/scripts/perm_audit.py`:

```python
"""Read-only permission audit. Run via `bench --site <site> console < perm_audit.py`.

Captures the effective (doctype -> permissions) map each persona's bundle grants on
THIS site. Two uses:
  - baseline: run before any bundle change and keep the JSON
  - diff: run after, and compare — every removal must be justified

Never writes. Safe on production.
"""

import json

import frappe

PERM_NAMES = ("read", "write", "create", "delete", "submit", "cancel", "select", "report", "export")


def _perm_table(doctype):
    """Custom DocPerm shadows DocPerm entirely once any row exists for a doctype."""
    if frappe.db.count("Custom DocPerm", {"parent": doctype}):
        return "Custom DocPerm"
    return "DocPerm"


def effective_perms(persona):
    """doctype -> set of permission names granted to `persona` by its bundle here."""
    from barakat.overrides.staff_roles import persona_role_bundle

    roles = set(persona_role_bundle(persona))
    if not roles:
        return {}

    out = {}
    for doctype in frappe.get_all("DocType", filters={"istable": 0}, pluck="name"):
        rows = frappe.get_all(
            _perm_table(doctype),
            filters={"parent": doctype, "permlevel": 0},
            fields=["role", *PERM_NAMES],
        )
        granted = set()
        for row in rows:
            if row.role not in roles:
                continue
            granted.update(p for p in PERM_NAMES if row.get(p))
        if granted:
            out[doctype] = granted
    return out


def snapshot():
    from barakat.permissions import PERSONAS

    return {
        "site": frappe.local.site,
        "personas": {
            persona: {dt: sorted(perms) for dt, perms in sorted(effective_perms(persona).items())}
            for persona in sorted(PERSONAS)
        },
    }


print("PERM_SNAPSHOT_JSON_START")
print(json.dumps(snapshot(), indent=2, sort_keys=True))
print("PERM_SNAPSHOT_JSON_END")
```

- [ ] **Step 3: Verify it imports cleanly without Frappe running**

Run: `cd C:/Users/IzTech-OTbaileh/Desktop/bar/barakat && python -c "import ast,sys; ast.parse(open('barakat/scripts/perm_audit.py').read()); print('parses')"`
Expected: `parses`

(A full import needs Frappe, which is not installed locally. Syntax is what we can check here; the script runs on the bench in Task 9.)

- [ ] **Step 4: Commit**

```bash
git add barakat/scripts/__init__.py barakat/scripts/perm_audit.py
git commit -m "feat(perms): read-only audit that captures a persona's effective DocPerms"
```

---

## Task 2: The module-to-doctype map and the matrix

**Files:**
- Create: `barakat/persona_matrix.py`
- Create: `barakat/persona_matrix.json`
- Test: `barakat/overrides/test_persona_guard.py` (append)

**Interfaces:**
- Produces:
  - `MODULE_DOCTYPES: dict[str, tuple[str, ...]]` — module key to doctypes.
  - `PERSONA_MATRIX: dict[str, dict[str, str]]` — persona to `{module: "none"|"read"|"write"}`.
  - `TILL_REQUIRED_READS: tuple[str, ...]` — doctypes the POS till reads, granted additively to Manager and Branch Supervisor.
  - `MODULE_KEYS: tuple[str, ...]` — every module key, matching `catalog.ts`.

- [ ] **Step 1: Write the failing test**

Append to `barakat/overrides/test_persona_guard.py`:

```python
from barakat.persona_matrix import (
    MODULE_DOCTYPES,
    MODULE_KEYS,
    PERSONA_MATRIX,
    TILL_REQUIRED_READS,
)


class PersonaMatrixData(unittest.TestCase):
    def test_every_module_key_has_a_doctype_list(self):
        for key in MODULE_KEYS:
            self.assertIn(key, MODULE_DOCTYPES, key)

    def test_every_persona_covers_every_module(self):
        for persona, row in PERSONA_MATRIX.items():
            for key in MODULE_KEYS:
                self.assertIn(key, row, f"{persona} missing {key}")
                self.assertIn(row[key], ("none", "read", "write"), f"{persona}.{key}")

    def test_personas_match_the_bundle_keys(self):
        self.assertEqual(set(PERSONA_MATRIX), set(PERSONA_ROLE_BUNDLES))

    def test_till_reads_are_declared(self):
        # The till pulls these under a Manager / Branch Supervisor device session.
        for doctype in ("System Settings", "Global Defaults", "Device", "POS Scale Settings"):
            self.assertIn(doctype, TILL_REQUIRED_READS, doctype)

    def test_matrix_matches_the_json_snapshot(self):
        import json
        import os

        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "persona_matrix.json")
        with open(path, encoding="utf-8") as fh:
            snapshot = json.load(fh)
        self.assertEqual(snapshot, PERSONA_MATRIX)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/IzTech-OTbaileh/Desktop/bar/barakat && python -m unittest barakat.overrides.test_persona_guard -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'barakat.persona_matrix'`

- [ ] **Step 3: Write the map**

Create `barakat/persona_matrix.py`. Transcribe `PERSONA_MATRIX` cell-for-cell from
`proxy-barakat/src/modules/roles/catalog.ts` — Manager is `allWrite()` with the seven
`reports.*` sub-keys forced to `read`.

```python
"""The admin-panel persona matrix, transcribed for the ERPNext side.

`proxy-barakat/src/modules/roles/catalog.ts` is the source of truth. This module is
its Python twin, kept honest by `persona_matrix.json`, a byte-identical snapshot
committed to BOTH repos with a test on each side. A hand-copy without that guard is
what broke this file once before.

Frappe-free on purpose: imported by pure unittests.
"""

MODULE_KEYS = (
    "dashboard", "pos", "products", "inventory", "warehouses", "branches",
    "staff", "roles", "attendance", "salary", "finance", "reports",
    "settings", "accounting", "customers", "suppliers",
    "reports.sales", "reports.products", "reports.inventory",
    "reports.staff", "reports.pos", "reports.salary", "reports.suppliers",
)

# Derived from the proxy's service code (every erp.list/get/create/update/delete call
# in src/modules/*) and the till's /api/resource/* paths. See the design doc's
# "Proving nothing is missing / A".
MODULE_DOCTYPES = {
    "dashboard": (),
    "pos": ("POS Invoice", "POS Opening Entry", "POS Closing Entry", "POS Profile",
            "POS Employee Branch", "Device"),
    "products": ("Item", "Item Group", "Item Price", "Product Bundle", "UOM",
                 "Price List", "Bin", "Pricing Rule"),
    "inventory": ("Stock Entry", "Stock Reconciliation", "Stock Ledger Entry", "Bin"),
    "warehouses": ("Warehouse",),
    "branches": ("Branch",),
    "staff": ("Employee", "Designation", "Holiday List", "Holiday List Assignment", "User"),
    "roles": ("Role",),
    "attendance": ("Attendance",),
    "salary": ("Salary Slip", "Salary Structure", "Salary Component",
               "Salary Structure Assignment", "Payroll Settings"),
    "finance": ("GL Entry", "Journal Entry", "Payment Entry", "Fiscal Year"),
    "reports": (),
    "settings": ("Company", "Global Defaults", "System Settings", "POS Scale Settings"),
    "accounting": ("Account", "Mode of Payment", "Sales Taxes and Charges Template",
                   "Currency", "Currency Exchange"),
    "customers": ("Customer", "Customer Group", "Contact", "Loyalty Program",
                  "Loyalty Point Entry", "Territory"),
    "suppliers": ("Supplier", "Supplier Group", "Purchase Invoice"),
    "reports.sales": ("Sales Invoice", "POS Invoice"),
    "reports.products": ("Item", "Bin"),
    "reports.inventory": ("Bin", "Warehouse", "Stock Ledger Entry"),
    "reports.staff": ("Employee", "Attendance"),
    "reports.pos": ("POS Invoice", "POS Closing Entry", "Branch"),
    "reports.salary": ("Salary Slip",),
    "reports.suppliers": ("GL Entry", "Supplier"),
}

# The desktop till pulls these under a Manager / Branch Supervisor device session.
# ADDITIVE to the matrix and READ-ONLY: Branch Supervisor is `settings: none`, so a
# strict mirror would break its tills — silently, because every till pull is wrapped
# in try/catch and falls back to defaults. Granted through `Barakat POS Operator`,
# which only those two personas hold.
TILL_REQUIRED_READS = (
    "System Settings", "Global Defaults", "Device", "POS Scale Settings",
    "Company", "Currency", "Branch", "UOM", "Sales Taxes and Charges Template",
    "Pricing Rule", "Product Bundle",
)

_ALL_WRITE = {key: "write" for key in MODULE_KEYS}
_ALL_WRITE.update({key: "read" for key in MODULE_KEYS if key.startswith("reports.")})

PERSONA_MATRIX = {
    "Manager": dict(_ALL_WRITE),
    "Branch Supervisor": {
        "dashboard": "none", "pos": "write", "products": "write", "inventory": "write",
        "warehouses": "read", "branches": "read", "staff": "read", "roles": "none",
        "attendance": "write", "salary": "none", "finance": "read", "reports": "read",
        "settings": "none", "accounting": "read", "customers": "write", "suppliers": "read",
        "reports.sales": "read", "reports.products": "read", "reports.inventory": "read",
        "reports.staff": "read", "reports.pos": "read", "reports.salary": "none",
        "reports.suppliers": "none",
    },
    "Cashier": {
        "dashboard": "none", "pos": "read", "products": "read", "inventory": "none",
        "warehouses": "none", "branches": "none", "staff": "none", "roles": "none",
        "attendance": "none", "salary": "none", "finance": "none", "reports": "none",
        "settings": "none", "accounting": "none", "customers": "read", "suppliers": "none",
        "reports.sales": "none", "reports.products": "none", "reports.inventory": "none",
        "reports.staff": "none", "reports.pos": "none", "reports.salary": "none",
        "reports.suppliers": "none",
    },
    "Accountant": {
        "dashboard": "none", "pos": "read", "products": "none", "inventory": "none",
        "warehouses": "none", "branches": "none", "staff": "none", "roles": "none",
        "attendance": "none", "salary": "read", "finance": "write", "reports": "read",
        "settings": "none", "accounting": "write", "customers": "read", "suppliers": "write",
        "reports.sales": "read", "reports.products": "none", "reports.inventory": "none",
        "reports.staff": "none", "reports.pos": "read", "reports.salary": "read",
        "reports.suppliers": "read",
    },
    "Inventory Keeper": {
        "dashboard": "none", "pos": "none", "products": "write", "inventory": "write",
        "warehouses": "write", "branches": "none", "staff": "none", "roles": "none",
        "attendance": "none", "salary": "none", "finance": "none", "reports": "read",
        "settings": "none", "accounting": "none", "customers": "none", "suppliers": "write",
        "reports.sales": "none", "reports.products": "read", "reports.inventory": "read",
        "reports.staff": "none", "reports.pos": "none", "reports.salary": "none",
        "reports.suppliers": "read",
    },
    "HR": {
        "dashboard": "none", "pos": "none", "products": "none", "inventory": "none",
        "warehouses": "none", "branches": "read", "staff": "read", "roles": "read",
        "attendance": "write", "salary": "write", "finance": "none", "reports": "read",
        "settings": "none", "accounting": "none", "customers": "none", "suppliers": "none",
        "reports.sales": "none", "reports.products": "none", "reports.inventory": "none",
        "reports.staff": "read", "reports.pos": "none", "reports.salary": "read",
        "reports.suppliers": "none",
    },
}
```

- [ ] **Step 4: Generate the JSON snapshot from the Python (never hand-write it)**

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/bar/barakat
python -c "import json; from barakat.persona_matrix import PERSONA_MATRIX; open('barakat/persona_matrix.json','w',encoding='utf-8').write(json.dumps(PERSONA_MATRIX, indent=2, sort_keys=True) + '\n')"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest barakat.overrides.test_persona_guard -v`
Expected: PASS, including `test_matrix_matches_the_json_snapshot`

- [ ] **Step 6: Verify the transcription against catalog.ts by eye**

Open `C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat/src/modules/roles/catalog.ts`
and compare all six rows cell by cell. This is a transcription; a typo here silently
grants or denies a module. Do not skip.

- [ ] **Step 7: Commit**

```bash
git add barakat/persona_matrix.py barakat/persona_matrix.json barakat/overrides/test_persona_guard.py
git commit -m "feat(perms): transcribe the AP persona matrix and module-doctype map"
```

---

## Task 3: Generate the module capability roles

**Files:**
- Modify: `barakat/permissions.py`
- Test: `barakat/overrides/test_persona_guard.py` (append)

**Interfaces:**
- Produces:
  - `role_name_for(module: str, level: str) -> str | None` — e.g. `("products", "write")` gives `"Barakat Products Writer"`; returns `None` for `"none"` or a module with no doctypes.
  - `MODULE_ROLE_PERMS: dict[str, dict[str, tuple[str, ...]]]` — generated, same shape as `BARAKAT_ROLE_PERMS`.
  - `READER_PERMS = ("read", "select")`, `WRITER_PERMS = ("read", "select", "write", "create", "delete")`

- [ ] **Step 1: Write the failing test**

```python
from barakat.permissions import (
    MODULE_ROLE_PERMS,
    READER_PERMS,
    WRITER_PERMS,
    role_name_for,
)


class GeneratedModuleRoles(unittest.TestCase):
    def test_role_naming(self):
        self.assertEqual(role_name_for("products", "write"), "Barakat Products Writer")
        self.assertEqual(role_name_for("products", "read"), "Barakat Products Reader")

    def test_dotted_module_naming(self):
        self.assertEqual(role_name_for("reports.salary", "read"), "Barakat Reports Salary Reader")

    def test_none_grants_no_role(self):
        self.assertIsNone(role_name_for("products", "none"))

    def test_module_without_doctypes_grants_no_role(self):
        # `dashboard` and `reports` are AP-only gates with no ERPNext doctype.
        self.assertIsNone(role_name_for("dashboard", "write"))
        self.assertIsNone(role_name_for("reports", "read"))

    def test_reader_grants_select_alongside_read(self):
        # Frappe's list query accepts `select` OR `read`; link pickers run on `select`
        # alone. Losing it empties dropdowns with no error anywhere.
        self.assertIn("select", READER_PERMS)
        perms = MODULE_ROLE_PERMS["Barakat Products Reader"]["Item"]
        self.assertEqual(set(perms), set(READER_PERMS))

    def test_writer_grants_the_full_lifecycle(self):
        perms = MODULE_ROLE_PERMS["Barakat Products Writer"]["Item"]
        self.assertEqual(set(perms), set(WRITER_PERMS))

    def test_generated_roles_cover_every_module_with_doctypes(self):
        for key, doctypes in MODULE_DOCTYPES.items():
            if not doctypes:
                continue
            self.assertIn(role_name_for(key, "read"), MODULE_ROLE_PERMS, key)
            self.assertIn(role_name_for(key, "write"), MODULE_ROLE_PERMS, key)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest barakat.overrides.test_persona_guard -v`
Expected: FAIL with `ImportError: cannot import name 'MODULE_ROLE_PERMS'`

- [ ] **Step 3: Implement in `barakat/permissions.py`**

Add near the top, after the existing `BARAKAT_ROLE_PERMS` block:

```python
from barakat.persona_matrix import MODULE_DOCTYPES, MODULE_KEYS, PERSONA_MATRIX, TILL_REQUIRED_READS

# `select` is granted alongside `read` on purpose. Frappe's list query permits
# `select` OR `read`, and link-field pickers rely on `select` alone — measured on
# niveen1, where a cashier lists 98 account NAMES with read denied. Dropping it
# empties dropdowns with no error in the AP and no error in the till.
READER_PERMS = ("read", "select")
WRITER_PERMS = ("read", "select", "write", "create", "delete")


def role_name_for(module, level):
    """The generated role for a (module, level) cell, or None when nothing is granted."""
    if level not in ("read", "write"):
        return None
    if not MODULE_DOCTYPES.get(module):
        return None
    words = " ".join(part.capitalize() for part in module.split("."))
    return f"Barakat {words} {'Writer' if level == 'write' else 'Reader'}"


def _build_module_role_perms():
    out = {}
    for module, doctypes in MODULE_DOCTYPES.items():
        if not doctypes:
            continue
        for level, perms in (("read", READER_PERMS), ("write", WRITER_PERMS)):
            out[role_name_for(module, level)] = {dt: perms for dt in doctypes}
    return out


MODULE_ROLE_PERMS = _build_module_role_perms()
```

Then extend the provisioning source so the generated roles are minted and permed by the
existing machinery. Replace the `BARAKAT_CUSTOM_ROLES` line:

```python
# Hand-written roles first, generated module roles second. A generated role never
# overwrites a hand-written one — asserted below.
_OVERLAP = set(BARAKAT_ROLE_PERMS).intersection(MODULE_ROLE_PERMS)
assert not _OVERLAP, f"generated role name collides with a hand-written one: {sorted(_OVERLAP)}"

ALL_ROLE_PERMS = {**BARAKAT_ROLE_PERMS, **MODULE_ROLE_PERMS}
BARAKAT_CUSTOM_ROLES = tuple(ALL_ROLE_PERMS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest barakat.overrides.test_persona_guard -v`
Expected: PASS

- [ ] **Step 5: Point the provisioning at the merged map**

In `barakat/setup/install.py`, change the import inside `_grant_barakat_role_perms` from
`BARAKAT_ROLE_PERMS` to `ALL_ROLE_PERMS`, and its loop variable accordingly:

```python
    from barakat.permissions import ALL_ROLE_PERMS

    for role, doctype_perms in ALL_ROLE_PERMS.items():
```

- [ ] **Step 6: Commit**

```bash
git add barakat/permissions.py barakat/setup/install.py barakat/overrides/test_persona_guard.py
git commit -m "feat(perms): generate a Barakat role per module capability"
```

---

## Task 4: Derive persona bundles from the matrix

**Files:**
- Modify: `barakat/permissions.py:300-394` (replace the hand-written `PERSONA_ROLE_BUNDLES`)
- Test: `barakat/overrides/test_persona_guard.py` (append)

**Interfaces:**
- Produces: `PERSONA_ROLE_BUNDLES: dict[str, tuple[str, ...]]`, same name and shape as today so `bundle_for` and every caller are unchanged.

- [ ] **Step 1: Write the failing test**

```python
NATIVE_ROLE_MARKERS = (
    "Accounts Manager", "Accounts User", "Sales Manager", "Sales Master Manager",
    "Sales User", "Stock Manager", "Stock User", "Item Manager", "Purchase Manager",
    "Purchase Master Manager", "Purchase User", "HR Manager", "HR User",
    "Employee", "Employee Self Service",
)


class BundlesDerivedFromMatrix(unittest.TestCase):
    def test_no_native_role_in_any_bundle(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            leaked = set(roles).intersection(NATIVE_ROLE_MARKERS)
            self.assertEqual(leaked, set(), f"{persona} still holds native roles: {sorted(leaked)}")

    def test_bundle_is_the_matrix_row(self):
        for persona, row in PERSONA_MATRIX.items():
            expected = {role_name_for(m, lvl) for m, lvl in row.items()}
            expected.discard(None)
            actual = set(PERSONA_ROLE_BUNDLES[persona])
            # Bundles also carry hand-written roles (POS Operator, Self Service);
            # every GENERATED role in the bundle must be exactly the matrix's.
            generated = {r for r in actual if r in MODULE_ROLE_PERMS}
            self.assertEqual(generated, expected, persona)

    def test_cashier_gets_no_salary_or_staff_role(self):
        cashier = set(PERSONA_ROLE_BUNDLES["Cashier"])
        self.assertNotIn("Barakat Salary Reader", cashier)
        self.assertNotIn("Barakat Salary Writer", cashier)
        self.assertNotIn("Barakat Staff Reader", cashier)

    def test_hr_keeps_salary_write(self):
        self.assertIn("Barakat Salary Writer", PERSONA_ROLE_BUNDLES["HR"])

    def test_till_personas_keep_the_pos_operator_role(self):
        for persona in ("Manager", "Branch Supervisor"):
            self.assertIn("Barakat POS Operator", PERSONA_ROLE_BUNDLES[persona], persona)

    def test_no_bundle_leaks_forbidden_role(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            self.assertEqual(FORBIDDEN_ROLES.intersection(roles), set(), persona)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest barakat.overrides.test_persona_guard -v`
Expected: FAIL on `test_no_native_role_in_any_bundle` — the current bundles are full of them.

- [ ] **Step 3: Replace the hand-written bundles**

In `barakat/permissions.py`, delete the literal `PERSONA_ROLE_BUNDLES = { ... }` block
(lines ~300-394) and replace with:

```python
# Hand-written roles a persona keeps ON TOP of its generated matrix roles. These are
# capabilities the matrix does not express:
#   - Barakat POS Operator: the till's own reads/writes (see TILL_REQUIRED_READS) and
#     the shift lifecycle. Only Manager and Branch Supervisor may log a device in.
#   - Barakat Self Service: every persona's own profile and own payslips, row-scoped.
#   - Barakat Staff Manager: creating logins / assigning role presets. Manager only.
#   - Barakat Purchase Invoice Clerk: Purchase Invoice submit for `suppliers: write`
#     personas that have no finance role to carry it.
#   - Barakat Supplier Ledger Reader: GL Entry scoped to supplier rows, for the
#     supplier statement (`reports.suppliers: read`) without granting the whole ledger.
EXTRA_ROLES = {
    "Manager": ("Barakat POS Operator", "Barakat Staff Manager", "Barakat Self Service",
                "Barakat Purchase Invoice Clerk"),
    "Branch Supervisor": ("Barakat POS Operator", "Barakat Self Service"),
    "Cashier": ("Barakat Self Service",),
    "Accountant": ("Barakat Self Service", "Barakat Purchase Invoice Clerk"),
    "Inventory Keeper": ("Barakat Self Service", "Barakat Purchase Invoice Clerk",
                         "Barakat Supplier Ledger Reader"),
    "HR": ("Barakat Self Service",),
}


def _build_persona_bundles():
    out = {}
    for persona, row in PERSONA_MATRIX.items():
        roles = []
        for module in MODULE_KEYS:
            name = role_name_for(module, row[module])
            if name and name not in roles:
                roles.append(name)
        for name in EXTRA_ROLES.get(persona, ()):
            if name not in roles:
                roles.append(name)
        out[persona] = tuple(roles)
    return out


PERSONA_ROLE_BUNDLES = _build_persona_bundles()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest barakat.overrides.test_persona_guard -v`
Expected: PASS. If `test_bundle_is_the_matrix_row` fails, the matrix and `EXTRA_ROLES` disagree — fix the data, not the test.

- [ ] **Step 5: Commit**

```bash
git add barakat/permissions.py barakat/overrides/test_persona_guard.py
git commit -m "feat(perms): derive persona bundles from the matrix, drop every native role"
```

---

## Task 5: Self-service scoping for Employee and Salary Slip

**Files:**
- Modify: `barakat/permissions.py` (add the scope helper + role perms)
- Create: `barakat/overrides/self_service.py`
- Modify: `barakat/hooks.py:310-317`
- Test: `barakat/overrides/test_persona_guard.py` (append)

**Interfaces:**
- Produces:
  - `SELF_SERVICE_ROLE = "Barakat Self Service"`
  - `UNSCOPED_BY_DOCTYPE: dict[str, frozenset[str]]`
  - `self_scope_applies(doctype: str, roles: Iterable[str]) -> bool` — True when the caller must be narrowed to their own rows.
  - `barakat.overrides.self_service.employee_query_conditions`, `.salary_slip_query_conditions`, `.employee_has_permission`, `.salary_slip_has_permission`

- [ ] **Step 1: Write the failing test**

```python
from barakat.permissions import SELF_SERVICE_ROLE, self_scope_applies


class SelfServiceScope(unittest.TestCase):
    def test_self_service_only_caller_is_scoped(self):
        self.assertTrue(self_scope_applies("Salary Slip", [SELF_SERVICE_ROLE]))
        self.assertTrue(self_scope_applies("Employee", [SELF_SERVICE_ROLE]))

    def test_hr_is_not_scoped(self):
        # THE trap: a permission_query_conditions hook applies to EVERY user of that
        # doctype. Scoping HR to its own record breaks payroll outright.
        hr = PERSONA_ROLE_BUNDLES["HR"]
        self.assertFalse(self_scope_applies("Salary Slip", hr))
        self.assertFalse(self_scope_applies("Employee", hr))

    def test_manager_is_not_scoped(self):
        manager = PERSONA_ROLE_BUNDLES["Manager"]
        self.assertFalse(self_scope_applies("Salary Slip", manager))
        self.assertFalse(self_scope_applies("Employee", manager))

    def test_cashier_is_scoped_on_both(self):
        cashier = PERSONA_ROLE_BUNDLES["Cashier"]
        self.assertTrue(self_scope_applies("Salary Slip", cashier))
        self.assertTrue(self_scope_applies("Employee", cashier))

    def test_caller_without_self_service_is_untouched(self):
        # An owner / System Manager holds neither role: the hook must stand down so
        # its query is exactly what it was before the hook existed.
        self.assertFalse(self_scope_applies("Employee", ["System Manager"]))

    def test_unrelated_doctype_never_scoped(self):
        self.assertFalse(self_scope_applies("Item", [SELF_SERVICE_ROLE]))

    def test_every_persona_holds_self_service(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            self.assertIn(SELF_SERVICE_ROLE, roles, persona)

    def test_preserved_roles_no_longer_carry_the_leak(self):
        self.assertNotIn("Employee", PRESERVED_ROLES)
        self.assertNotIn("Employee Self Service", PRESERVED_ROLES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest barakat.overrides.test_persona_guard -v`
Expected: FAIL with `ImportError: cannot import name 'SELF_SERVICE_ROLE'`

- [ ] **Step 3: Implement the pure logic in `barakat/permissions.py`**

```python
SELF_SERVICE_ROLE = "Barakat Self Service"

# Roles whose holders read these doctypes UNSCOPED. A permission_query_conditions
# hook applies to every user of a doctype, not just the persona it was written for —
# without this stand-down list, HR and Manager would be narrowed to their own record
# and payroll would break outright.
UNSCOPED_BY_DOCTYPE = {
    "Employee": frozenset({"Barakat Staff Reader", "Barakat Staff Writer",
                           "Barakat Attendance Manager", "System Manager", "Administrator"}),
    "Salary Slip": frozenset({"Barakat Salary Reader", "Barakat Salary Writer",
                              "Barakat Reports Salary Reader", "System Manager", "Administrator"}),
}


def self_scope_applies(doctype, roles):
    """True when this caller must be narrowed to their own rows on `doctype`."""
    unscoped = UNSCOPED_BY_DOCTYPE.get(doctype)
    if unscoped is None:
        return False
    role_set = set(roles)
    if role_set.intersection(unscoped):
        return False
    return SELF_SERVICE_ROLE in role_set
```

Add the role's perms to `BARAKAT_ROLE_PERMS`:

```python
	# Replaces the native `Employee` / `Employee Self Service` roles, which granted
	# UNSCOPED read on Employee and Salary Slip to every persona — measured on prod
	# 2026-07-29, a Cashier read every salary slip (net_pay 2884.62) and all 138
	# employee records. Stock ERPNext pairs those roles with a User Permission pinning
	# the user to their own Employee; barakat deletes it deliberately
	# (overrides/staff_roles.py:158), which left the read unrestricted.
	#
	# Row scoping lives in overrides/self_service.py, NOT here — a DocPerm is
	# per-doctype and cannot express "only my own".
	"Barakat Self Service": {
		"Employee": ("read", "select"),
		"Salary Slip": ("read",),
	},
```

And drop the leak from `PRESERVED_ROLES`:

```python
# ERPNext re-attaches `Employee` when an Employee record links to a User, so it cannot
# be permanently prevented — but it is no longer PRESERVED. `reassert_persona_roles`
# rewrites the roles child table to exactly the bundle on every Employee save, which
# strips it again. Self-service reads come from SELF_SERVICE_ROLE instead.
PRESERVED_ROLES = frozenset()
```

- [ ] **Step 4: Write the Frappe hooks**

Create `barakat/overrides/self_service.py`, mirroring `overrides/gl_entry.py`:

```python
"""Row-level scoping for the `Barakat Self Service` role.

The role grants read on Employee and Salary Slip so every persona keeps its own
profile and payslips. A DocPerm is per-doctype, so that read alone would expose the
whole staff directory and every salary — which is exactly the production finding this
replaces. These hooks narrow it to the caller's own rows: the list query through the
SQL condition, a single document through the controller-permission hook.

Both are required. Shipping only the query condition leaves a direct document read
open, which is the difference between the Account false alarm (list-only, harmless)
and the Salary Slip finding (full document, real).

Callers holding an unscoped role for that doctype (HR, Manager, owners) are left
completely alone; see `barakat.permissions.self_scope_applies`.
"""

import frappe

from barakat.permissions import self_scope_applies


def _own_employee_names(user):
    """Employee docnames linked to this user. Empty tuple when they have none."""
    return tuple(frappe.get_all("Employee", filters={"user_id": user}, pluck="name"))


def _in_clause(names):
    """SQL IN list. `('')` when empty so the condition matches nothing rather than
    degenerating into valid-but-unfiltered SQL."""
    if not names:
        return "('')"
    escaped = ", ".join(frappe.db.escape(n) for n in names)
    return f"({escaped})"


def employee_query_conditions(user=None, doctype=None):
    user = user or frappe.session.user
    if not self_scope_applies("Employee", frappe.get_roles(user)):
        return ""
    return f"`tabEmployee`.`name` in {_in_clause(_own_employee_names(user))}"


def salary_slip_query_conditions(user=None, doctype=None):
    user = user or frappe.session.user
    if not self_scope_applies("Salary Slip", frappe.get_roles(user)):
        return ""
    return f"`tabSalary Slip`.`employee` in {_in_clause(_own_employee_names(user))}"


def _field(doc, name):
    return doc.get(name) if isinstance(doc, dict) else getattr(doc, name, None)


def employee_has_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    if not self_scope_applies("Employee", frappe.get_roles(user)):
        return True
    return _field(doc, "name") in _own_employee_names(user)


def salary_slip_has_permission(doc, ptype="read", user=None, **kwargs):
    user = user or frappe.session.user
    if not self_scope_applies("Salary Slip", frappe.get_roles(user)):
        return True
    return _field(doc, "employee") in _own_employee_names(user)
```

- [ ] **Step 5: Register the hooks**

In `barakat/hooks.py`, extend the existing dicts at lines 310-317:

```python
permission_query_conditions = {
	"GL Entry": "barakat.overrides.gl_entry.get_permission_query_conditions",
	"Employee": "barakat.overrides.self_service.employee_query_conditions",
	"Salary Slip": "barakat.overrides.self_service.salary_slip_query_conditions",
}

has_permission = {
	"GL Entry": "barakat.overrides.gl_entry.has_permission",
	"Employee": "barakat.overrides.self_service.employee_has_permission",
	"Salary Slip": "barakat.overrides.self_service.salary_slip_has_permission",
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m unittest barakat.overrides.test_persona_guard -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add barakat/permissions.py barakat/overrides/self_service.py barakat/hooks.py barakat/overrides/test_persona_guard.py
git commit -m "fix(perms): scope Employee and Salary Slip reads to the caller's own records"
```

---

## Task 6: The matrix guard test

**Files:**
- Create: `barakat/test_persona_matches_matrix.py`

This runs under the bench test runner (it needs the DocPerm table), unlike the pure tests.

**Interfaces:**
- Consumes: `effective_perms` from Task 1, `PERSONA_MATRIX` / `MODULE_DOCTYPES` / `TILL_REQUIRED_READS` from Task 2.

- [ ] **Step 1: Write the test**

Create `barakat/test_persona_matches_matrix.py`:

```python
"""The guard: a persona's ERPNext DocPerms must equal its AP matrix row.

Runs under the bench test runner because it reads the site's DocPerm table:
    bench --site <site> run-tests --module barakat.test_persona_matches_matrix

Without this test the two layers drift the moment someone adds a doctype, which is
exactly how the production finding of 2026-07-29 survived for months.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.permissions import PERSONA_ROLE_BUNDLES
from barakat.persona_matrix import MODULE_DOCTYPES, PERSONA_MATRIX, TILL_REQUIRED_READS
from barakat.scripts.perm_audit import effective_perms

WRITE_PERMS = {"write", "create", "delete", "submit", "cancel"}

# Doctypes a persona may hold beyond its matrix, with the reason. Anything else is a leak.
ALLOWED_EXTRA = {
    "Manager": set(TILL_REQUIRED_READS) | {"Employee", "Salary Slip", "Purchase Invoice"},
    "Branch Supervisor": set(TILL_REQUIRED_READS) | {"Employee", "Salary Slip"},
    "Cashier": {"Employee", "Salary Slip"},
    "Accountant": {"Employee", "Salary Slip", "Purchase Invoice"},
    "Inventory Keeper": {"Employee", "Salary Slip", "Purchase Invoice", "GL Entry", "Supplier"},
    "HR": {"Employee", "Salary Slip"},
}


def _matrix_allows(persona, doctype):
    """(may_read, may_write) for this doctype under the persona's matrix row."""
    may_read = may_write = False
    for module, level in PERSONA_MATRIX[persona].items():
        if doctype not in MODULE_DOCTYPES.get(module, ()):
            continue
        if level in ("read", "write"):
            may_read = True
        if level == "write":
            may_write = True
    return may_read, may_write


class PersonaMatchesMatrix(FrappeTestCase):
    def test_no_persona_exceeds_its_matrix(self):
        for persona in PERSONA_ROLE_BUNDLES:
            for doctype, perms in effective_perms(persona).items():
                if doctype in ALLOWED_EXTRA[persona]:
                    continue
                may_read, may_write = _matrix_allows(persona, doctype)
                self.assertTrue(
                    may_read,
                    f"{persona} reaches {doctype} ({sorted(perms)}) with no matrix module granting it",
                )
                if perms & WRITE_PERMS:
                    self.assertTrue(
                        may_write,
                        f"{persona} WRITES {doctype} but its matrix says read-only",
                    )

    def test_every_matrix_grant_is_actually_reachable(self):
        """The other direction: a bundle that is too NARROW fails silently in the AP
        (empty dropdown) and in the till (falls back to defaults). Catch it here."""
        for persona, row in PERSONA_MATRIX.items():
            effective = effective_perms(persona)
            for module, level in row.items():
                if level == "none":
                    continue
                for doctype in MODULE_DOCTYPES.get(module, ()):
                    if not frappe.db.exists("DocType", doctype):
                        continue
                    granted = effective.get(doctype, set())
                    self.assertTrue(
                        granted & {"read", "select"},
                        f"{persona} has {module}: {level} but no read on {doctype}",
                    )
                    if level == "write":
                        self.assertTrue(
                            granted & WRITE_PERMS,
                            f"{persona} has {module}: write but no write on {doctype}",
                        )

    def test_cashier_cannot_reach_salary_or_staff_modules(self):
        effective = effective_perms("Cashier")
        # Present via Barakat Self Service, but row-scoped — see test_self_service_scope.
        self.assertNotIn("Salary Structure", effective)
        self.assertNotIn("Payroll Settings", effective)
        self.assertNotIn("Designation", effective)

    def test_till_doctypes_readable_by_both_device_personas(self):
        for persona in ("Manager", "Branch Supervisor"):
            effective = effective_perms(persona)
            for doctype in TILL_REQUIRED_READS:
                if not frappe.db.exists("DocType", doctype):
                    continue
                self.assertTrue(
                    effective.get(doctype, set()) & {"read", "select"},
                    f"{persona} till would silently fall back: no read on {doctype}",
                )
```

- [ ] **Step 2: Verify it parses**

Run: `python -c "import ast; ast.parse(open('barakat/test_persona_matches_matrix.py').read()); print('parses')"`
Expected: `parses`

(It cannot run locally — no Frappe. It runs on the bench in Task 9.)

- [ ] **Step 3: Commit**

```bash
git add barakat/test_persona_matches_matrix.py
git commit -m "test(perms): guard that every persona's DocPerms equal its matrix row"
```

---

## Task 7: Revoke stale permissions on migrate

Without this the whole change no-ops on already-migrated sites: `update_permission_property`
only ever sets a perm to `1`.

**Files:**
- Modify: `barakat/setup/install.py`

**Interfaces:**
- Produces: `_revoke_stale_barakat_perms()`, added to the `after_migrate` list.

- [ ] **Step 1: Implement**

Add to `barakat/setup/install.py`:

```python
def _revoke_stale_barakat_perms():
	"""Clear permissions a `Barakat *` role no longer declares.

	`_grant_barakat_role_perms` only ever sets a perm to 1, so a role that NARROWS
	between releases keeps its old grants on every already-migrated site. Harmless
	while roles only widen; this is the pass that makes narrowing real.

	Only touches roles this app owns (name starts with "Barakat "). Native ERPNext
	roles and any tenant-defined role are never modified.
	"""
	from frappe.permissions import update_permission_property

	from barakat.permissions import ALL_ROLE_PERMS

	all_perms = ("read", "write", "create", "delete", "submit", "cancel", "select", "report", "export")

	for role, doctype_perms in ALL_ROLE_PERMS.items():
		rows = frappe.get_all(
			"Custom DocPerm",
			filters={"role": role, "permlevel": 0},
			fields=["name", "parent"],
		)
		for row in rows:
			wanted = set(doctype_perms.get(row.parent, ()))
			for perm in all_perms:
				if perm in wanted:
					continue
				update_permission_property(row.parent, role, 0, perm, 0, validate=False)
			frappe.clear_cache(doctype=row.parent)
```

- [ ] **Step 2: Wire it into `after_migrate`, after the grant pass**

In the `after_migrate` list in `barakat/setup/install.py`, add `_revoke_stale_barakat_perms`
immediately after `_grant_barakat_role_perms`:

```python
		_grant_barakat_role_perms,
		_revoke_stale_barakat_perms,
```

- [ ] **Step 3: Verify it parses**

Run: `python -c "import ast; ast.parse(open('barakat/setup/install.py').read()); print('parses')"`
Expected: `parses`

- [ ] **Step 4: Commit**

```bash
git add barakat/setup/install.py
git commit -m "fix(perms): revoke permissions a Barakat role no longer declares"
```

---

## Task 8: Auto-apply the backfill on migrate

**Files:**
- Modify: `barakat/setup/install.py:64-78` (the note explaining why the backfill is NOT wired in) and the `after_migrate` list.

- [ ] **Step 1: Add the before/after diff log**

Replace `_backfill_persona_roles` in `barakat/setup/install.py` with a logging version:

```python
def _backfill_persona_roles():
	"""Bring existing persona users onto the allow-list bundle.

	Wired into after_migrate as of 2026-07-29. It was deliberately manual before,
	because a subtractive change on an unverified tenant is a real risk. What makes it
	acceptable now: the bundles are derived mechanically from the matrix and guarded by
	test_persona_matches_matrix, and every change is written to the Error Log below —
	which is the only way to see afterwards what a migrate actually did to a site.
	"""
	from barakat.overrides.staff_roles import reassert_persona_roles
	from barakat.permissions import PERSONAS

	employees = frappe.get_all(
		"Employee",
		filters={"custom_role_preset": ("in", sorted(PERSONAS)), "user_id": ("is", "set")},
		pluck="name",
	)
	changes = []
	for name in employees:
		try:
			doc = frappe.get_doc("Employee", name)
			email = (doc.user_id or "").strip()
			before = set(frappe.get_all("Has Role", filters={"parent": email}, pluck="role"))
			reassert_persona_roles(doc)
			after = set(frappe.get_all("Has Role", filters={"parent": email}, pluck="role"))
			if before != after:
				changes.append(
					f"{email} ({doc.custom_role_preset}): "
					f"-{sorted(before - after)} +{sorted(after - before)}"
				)
		except Exception as e:
			frappe.log_error(f"barakat backfill: {name} failed: {e}", "Persona roles")

	if changes:
		frappe.log_error(
			f"barakat persona backfill on {frappe.local.site}:\n" + "\n".join(changes),
			"Persona roles backfill",
		)
```

- [ ] **Step 2: Wire it into `after_migrate`, last**

It must run after roles are provisioned and permed. Add to the end of the `after_migrate`
list:

```python
		_relax_demo_company_user_perm,
		_backfill_persona_roles,
```

Then replace the "NOT wired into after_migrate" comment block above
`backfill_persona_roles()` with a note that it now runs automatically and the public entry
point remains for manual re-runs.

- [ ] **Step 3: Verify it parses**

Run: `python -c "import ast; ast.parse(open('barakat/setup/install.py').read()); print('parses')"`
Expected: `parses`

- [ ] **Step 4: Commit**

```bash
git add barakat/setup/install.py
git commit -m "feat(perms): apply the persona backfill on migrate, with a change log"
```

---

## Task 9: Removal diff against a real site (READ-ONLY, no deploy)

The primary safety net. Produces the removal table the owner reviews before any deploy.

**Files:**
- Create: `docs/superpowers/plans/2026-07-29-removal-table.md`

- [ ] **Step 1: Capture the baseline from the test site**

```bash
scp -i ~/.ssh/barakat-test.pem barakat/scripts/perm_audit.py ubuntu@52.59.253.35:/tmp/perm_audit.py
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 "sudo cp /tmp/perm_audit.py /home/frappe/erp_project/pa.py && sudo chown frappe:frappe /home/frappe/erp_project/pa.py && cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench --site qa-test.test.barakat.iztech.net console < pa.py" > /tmp/baseline-qa-test.txt
```

Extract the JSON between the `PERM_SNAPSHOT_JSON_START` / `_END` markers and save it as
`docs/superpowers/plans/baseline-qa-test.json`.

**Do not migrate. Do not pull the app on the server.** This reads the site as it is today.

- [ ] **Step 2: Compute the proposed set locally**

The proposed set is derivable without a site: for each persona, union the perms of every
role in `PERSONA_ROLE_BUNDLES[persona]` as declared in `ALL_ROLE_PERMS`.

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/bar/barakat
python - <<'PY'
import json
from barakat.permissions import ALL_ROLE_PERMS, PERSONA_ROLE_BUNDLES

proposed = {}
for persona, roles in PERSONA_ROLE_BUNDLES.items():
    merged = {}
    for role in roles:
        for dt, perms in ALL_ROLE_PERMS.get(role, {}).items():
            merged.setdefault(dt, set()).update(perms)
    proposed[persona] = {dt: sorted(p) for dt, p in sorted(merged.items())}
print(json.dumps(proposed, indent=2, sort_keys=True))
PY
```

Save as `docs/superpowers/plans/proposed.json`.

- [ ] **Step 3: Diff and write the removal table**

```bash
python - <<'PY'
import json

baseline = json.load(open("docs/superpowers/plans/baseline-qa-test.json"))["personas"]
proposed = json.load(open("docs/superpowers/plans/proposed.json"))

for persona in sorted(baseline):
    old, new = baseline[persona], proposed.get(persona, {})
    lost = []
    for dt, perms in sorted(old.items()):
        gone = set(perms) - set(new.get(dt, []))
        if gone:
            lost.append((dt, sorted(gone)))
    print(f"\n## {persona} — {len(lost)} doctypes lose permissions")
    for dt, gone in lost:
        print(f"- `{dt}`: {gone}")
PY
```

- [ ] **Step 4: Justify every removal**

Write `docs/superpowers/plans/2026-07-29-removal-table.md` with one row per removal and the
matrix cell that forbids it. **Any removal you cannot justify is a bug in the new bundle,
not an acceptable loss.** Resolve it before proceeding.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs(perms): removal table and permission baseline for review"
```

---

## Task 10: Proxy-side matrix snapshot test

**Files:**
- Create: `proxy-barakat/src/modules/roles/persona-matrix.json`
- Create: `proxy-barakat/src/modules/roles/matrix-snapshot.test.ts`

**Interfaces:**
- Consumes: `ROLE_CATALOG` from `./catalog`.

- [ ] **Step 1: Copy the snapshot**

```bash
cp C:/Users/IzTech-OTbaileh/Desktop/bar/barakat/barakat/persona_matrix.json \
   C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat/src/modules/roles/persona-matrix.json
```

- [ ] **Step 2: Write the test**

Create `proxy-barakat/src/modules/roles/matrix-snapshot.test.ts`:

```typescript
import { describe, expect, test } from 'bun:test'
import { ROLE_CATALOG } from './catalog'
import snapshot from './persona-matrix.json'

/**
 * The barakat Frappe app derives every persona's ERPNext role bundle from a Python
 * twin of this matrix. The two live in different repos, so they are kept honest by
 * this byte-identical snapshot: change catalog.ts without copying the regenerated
 * persona-matrix.json into both repos and this test goes red.
 *
 * Regenerate from the barakat repo:
 *   python -c "import json; from barakat.persona_matrix import PERSONA_MATRIX; \
 *     print(json.dumps(PERSONA_MATRIX, indent=2, sort_keys=True))"
 */
describe('persona matrix snapshot', () => {
  test('catalog.ts matches the snapshot shared with the barakat app', () => {
    const fromCatalog = Object.fromEntries(
      ROLE_CATALOG.map((r) => [r.role, r.modules]),
    )
    expect(fromCatalog).toEqual(snapshot as Record<string, Record<string, string>>)
  })
})
```

- [ ] **Step 3: Run the test**

Run: `cd C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat && bun test src/modules/roles/matrix-snapshot.test.ts`
Expected: PASS. If it fails, the Python transcription in Task 2 is wrong — fix the Python, regenerate the JSON, copy to both repos.

- [ ] **Step 4: Commit ONLY these two files**

The proxy repo has 4 unrelated uncommitted changes. Stage by path, never `git add -A`.

```bash
cd C:/Users/IzTech-OTbaileh/Desktop/barakat-repos/proxy-barakat
git add src/modules/roles/persona-matrix.json src/modules/roles/matrix-snapshot.test.ts
git commit -m "test(roles): fail when catalog.ts drifts from the barakat app's matrix copy"
```

**Do not push.** Pushing the proxy to `test` or `main` deploys it.

---

## Task 11: Bench verification on the test site (NO MIGRATE, NO DEPLOY)

Everything so far is unproven against a real DocPerm table. This task proves it **without
changing any site**, using a throwaway bench that is not test or prod.

- [ ] **Step 1: Ask the owner which bench may be used**

The plan cannot proceed past here without an answer. Options: a local Frappe bench, a
scratch site on the test box created for this purpose, or explicit approval to migrate
`qa-test`. **Do not choose unilaterally.**

- [ ] **Step 2: On the approved bench, install the branch and migrate**

```bash
sudo -u frappe git -C apps/barakat fetch origin dev && sudo -u frappe git -C apps/barakat checkout dev
sudo -u frappe bench --site <scratch-site> migrate
sudo -u frappe bench restart
```

- [ ] **Step 3: Run the guard tests**

```bash
sudo -u frappe bench --site <scratch-site> run-tests --module barakat.test_persona_matches_matrix
sudo -u frappe bench --site <scratch-site> run-tests --module barakat.test_self_service_scope
```

Expected: PASS. A failure in `test_every_matrix_grant_is_actually_reachable` means the
bundle is too narrow — the silent-failure case. Fix `MODULE_DOCTYPES`, do not relax the test.

- [ ] **Step 4: Prove the original finding is closed**

Re-run the exact probe that found the bug, as a Cashier on the scratch site:

```python
import frappe
frappe.set_user("<a cashier on this site>")
print("salary slips visible:", len(frappe.get_list("Salary Slip", fields=["name"], limit_page_length=0)))
print("employees visible:", len(frappe.get_list("Employee", fields=["name"], limit_page_length=0)))
names = frappe.get_all("Salary Slip", pluck="name", limit_page_length=1)
try:
    frappe.get_doc("Salary Slip", names[0]).check_permission("read")
    print("OPEN-DOC Salary Slip -> STILL ALLOWED (BUG)")
except Exception as e:
    print("OPEN-DOC Salary Slip -> denied", type(e).__name__)
```

Expected: salary slips visible = only their own (0 if they have none), employees visible = 1,
and the direct open denied for anyone else's slip.

- [ ] **Step 5: Walk the AP forms per persona**

An API sweep cannot see an empty dropdown. Start the AP and proxy locally
(proxy first on 8099, AP second on 3000 — never change the ports), sign in as each of the
six personas, and confirm every picker on every form the persona can open populates.

- [ ] **Step 6: Run a real POS shift**

On a Manager device session and again on a Branch Supervisor device session: open a shift,
sell a weighed (scale-barcode) item and a bundle, redeem loyalty points, close the shift.
The till fails silently, so a passing test is not sufficient evidence here.

- [ ] **Step 7: Report to the owner and STOP**

Summarise: guard tests, the closed finding, the removal table, the AP walk and the two POS
shifts. **Deployment to test or prod requires explicit owner approval.**

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Module to doctype map | 2 |
| Generated roles, no native roles | 3, 4 |
| Self-service replaces the Employee roles | 5 |
| `permission_query_conditions` stand-down for HR/Manager | 5 (`self_scope_applies`, tested) |
| Cross-repo matrix sync | 2 (JSON), 10 (proxy test) |
| Proving nothing is missing / A static derivation | 2 (`MODULE_DOCTYPES`) |
| Proving nothing is missing / B removal diff | 1, 9 |
| Proving nothing is missing / C `select` preservation | 3 (`READER_PERMS`, tested) |
| Proving nothing is missing / D child tables | 1 (`istable: 0` filter) |
| Proving nothing is missing / E runtime walks | 11 |
| Guard tests | 6, 10, 11 |
| Auto-backfill on migrate + Error Log diff | 8 |
| Rollout gated on owner approval | Global Constraints, 11 |

**Gaps found and closed during review:**
- `update_permission_property` never revokes — added as Task 7. Without it the change silently no-ops on every already-migrated site.
- The POS till needs reads the matrix does not grant — added `TILL_REQUIRED_READS` in Task 2 and asserted in Task 6, rather than letting a strict mirror break Branch Supervisor tills.
- `test_self_service_scope.py` is referenced by the spec and Task 11 but its content is written in Task 5's pure tests. The bench-level version asserting real row counts is exercised in Task 11 Step 4 rather than as a separate file — noted so it is not mistaken for a missing file.

**Type consistency:** `effective_perms` (Task 1) is consumed by Task 6 and returns
`dict[str, set[str]]` in both. `role_name_for` (Task 3) is consumed by Task 4 with the same
signature. `self_scope_applies(doctype, roles)` (Task 5) is called with that argument order
in `overrides/self_service.py`. `ALL_ROLE_PERMS` (Task 3) is consumed by Tasks 7 and 9.
