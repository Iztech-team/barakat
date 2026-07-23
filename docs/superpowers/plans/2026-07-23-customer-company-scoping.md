# Customer Company Scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the only remaining cross-company data leak — customers — by turning the Customer company/branch markers into real `Link` fields, and stop the tenant boundary from depending on a single ERPNext checkbox.

**Architecture:** Frappe enforces user permissions by scanning a doctype's `Link` fields and matching each field's `options` against the user's `User Permission` rows (`frappe/model/db_query.py::add_user_permissions`). Every barakat company marker is already `Link → Company` **except** Customer's, which are `Data` and therefore invisible to that scan. Converting them makes the existing, proven mechanism cover customers. Separately, a hook re-asserts the Company `User Permission` on every Employee save so unticking `create_user_permission` can no longer silently unscope a user.

**Tech Stack:** Python 3, Frappe framework, `unittest` (+ `frappe.tests.utils.FrappeTestCase` for on-bench tests).

## Global Constraints

- **Backend only.** No changes to `proxy-barakat`, `admin_panel_barakat`, or the POS.
- **Branch:** work on `dev`, commit per task.
- **Add-only for permissions.** The hook must NEVER delete a `User Permission` — staff may legitimately span shops, and hand-granted extra companies must survive.
- **Patch must log, never guess.** A value that matches no Company/Branch is left alone and reported. Silent blanking is forbidden.
- **Test environment:** this is a Windows dev box with no Frappe — `import frappe` fails locally. **Pure tests** (JSON/dict only) run locally with `/c/Python313/python -m unittest` from the repo root `C:/Users/IzTech-OTbaileh/Desktop/bar/barakat`. **Frappe tests** run only on a bench: `bench --site <site> run-tests --module <module>` (prod sites need `bench --site <site> set-config allow_tests true` first, and reverting after).
- Field labels stay exactly as they are: `Company (Barakat)` and `Branch (Barakat)`.

---

### Task 1: Convert the Customer markers to Link fields

**Files:**
- Modify: `barakat/fixtures/custom_field.json` (the `Customer-custom_company`, `Customer-custom_branch` entries; add a `Customer Group-custom_company` entry)
- Create: `barakat/test_custom_fields.py`

**Interfaces:**
- Produces: fixture entries `Customer-custom_company` (`Link`→`Company`), `Customer-custom_branch` (`Link`→`Branch`), `Customer Group-custom_company` (`Link`→`Company`). Task 3's patch relies on these field names.

- [ ] **Step 1: Write the failing test**

Create `barakat/test_custom_fields.py`:

```python
"""Pure, Frappe-free guard on the company/branch marker custom fields.

Frappe enforces user permissions by scanning a doctype's LINK fields and matching
each field's `options` against the user's permissions
(frappe/model/db_query.py::add_user_permissions). A `Data` marker is not a link
field, so it is invisible to that scan and CANNOT be enforced — which is exactly
how 2,326 customers stayed readable across companies. This test makes that
mistake impossible to reintroduce.

Runs locally:  python -m unittest barakat.test_custom_fields
"""

import json
import pathlib
import unittest

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "custom_field.json"


def _rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_name(rows, name):
    return next((f for f in rows if f.get("name") == name), None)


class CompanyMarkersAreEnforceable(unittest.TestCase):
    def setUp(self):
        self.rows = _rows()

    def test_no_company_or_branch_marker_is_data(self):
        offenders = sorted(
            f"{f.get('dt')}.{f.get('fieldname')}"
            for f in self.rows
            if f.get("fieldtype") == "Data"
            and ("company" in str(f.get("fieldname", "")).lower()
                 or "branch" in str(f.get("fieldname", "")).lower())
        )
        self.assertEqual(
            offenders, [], f"Data markers can never be enforced by user permissions: {offenders}"
        )

    def test_customer_company_links_to_company(self):
        f = _by_name(self.rows, "Customer-custom_company")
        self.assertIsNotNone(f, "Customer-custom_company missing from fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Company")

    def test_customer_branch_links_to_branch(self):
        f = _by_name(self.rows, "Customer-custom_branch")
        self.assertIsNotNone(f, "Customer-custom_branch missing from fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Branch")

    def test_customer_group_company_is_shipped(self):
        # Exists on live sites as a proper Link, but was created by hand and never
        # added to the fixtures — a fresh site would come up without it and customer
        # groups would silently stop being scoped.
        f = _by_name(self.rows, "Customer Group-custom_company")
        self.assertIsNotNone(f, "Customer Group-custom_company is not shipped in fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Company")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python313/python -m unittest barakat.test_custom_fields -v`
Expected: FAIL — 4 failures: the two Customer markers are `Data`, and `Customer Group-custom_company` is missing.

- [ ] **Step 3: Write minimal implementation**

In `barakat/fixtures/custom_field.json`, replace the `Customer-custom_company` entry:

```json
  {
    "doctype": "Custom Field",
    "name": "Customer-custom_company",
    "dt": "Customer",
    "fieldname": "custom_company",
    "label": "Company (Barakat)",
    "fieldtype": "Link",
    "options": "Company",
    "insert_after": "customer_group",
    "in_list_view": 0,
    "in_standard_filter": 0
  },
```

Replace the `Customer-custom_branch` entry:

```json
  {
    "doctype": "Custom Field",
    "name": "Customer-custom_branch",
    "dt": "Customer",
    "fieldname": "custom_branch",
    "label": "Branch (Barakat)",
    "fieldtype": "Link",
    "options": "Branch",
    "insert_after": "custom_company",
    "in_list_view": 0,
    "in_standard_filter": 0
  },
```

And add a new entry immediately after the `Customer-custom_branch` entry:

```json
  {
    "doctype": "Custom Field",
    "name": "Customer Group-custom_company",
    "dt": "Customer Group",
    "fieldname": "custom_company",
    "label": "Company (Barakat)",
    "fieldtype": "Link",
    "options": "Company",
    "insert_after": "customer_group_name",
    "in_list_view": 0,
    "in_standard_filter": 0
  },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python313/python -m unittest barakat.test_custom_fields -v`
Expected: PASS (4 tests).

Also confirm the JSON is still valid and the previously-green suites still pass:
`/c/Python313/python -c "import json;json.load(open('barakat/fixtures/custom_field.json',encoding='utf-8'));print('json ok')"`
`/c/Python313/python -m unittest barakat.overrides.test_persona_guard`  → PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add barakat/fixtures/custom_field.json barakat/test_custom_fields.py
git commit -m "fix(scoping): make Customer company/branch markers real Link fields"
```

---

### Task 2: Re-assert the Company User Permission on every Employee save

**Files:**
- Modify: `barakat/overrides/staff_roles.py` (add `reassert_company_user_permission` above `reassert_persona_roles`)
- Modify: `barakat/hooks.py` (Employee `after_insert` / `on_update` become lists)
- Modify: `barakat/overrides/test_staff_roles.py` (append a test class)

**Interfaces:**
- Consumes: `PERSONAS` from `barakat.permissions` (already imported in `staff_roles.py`).
- Produces: `reassert_company_user_permission(doc, method=None) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `barakat/overrides/test_staff_roles.py`:

```python
class ReassertCompanyUserPermission(FrappeTestCase):
    """The tenant boundary must not depend on Employee.create_user_permission.

    ERPNext deletes the Company User Permission when that checkbox is unticked, and
    its label only mentions employee records — so the company wall can be dropped by
    accident. This hook re-asserts it on every save, add-only.
    """

    def _emp(self, preset="Cashier", user="staff@example.com", company="ACME"):
        return frappe._dict(
            {"custom_role_preset": preset, "user_id": user, "company": company}
        )

    def _exists(self, permission_present):
        def side_effect(doctype, *args, **kwargs):
            if doctype == "User":
                return True
            if doctype == "User Permission":
                return permission_present
            return False

        return side_effect

    def test_creates_the_permission_when_missing(self):
        with patch("frappe.db.exists", side_effect=self._exists(False)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp())
        add.assert_called_once_with("Company", "ACME", "staff@example.com")

    def test_is_a_noop_when_already_present(self):
        with patch("frappe.db.exists", side_effect=self._exists(True)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp())
        add.assert_not_called()

    def test_skips_unrecognised_preset(self):
        with patch("frappe.db.exists", side_effect=self._exists(False)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp(preset="Not A Persona"))
        add.assert_not_called()

    def test_skips_when_no_login_or_no_company(self):
        with patch("frappe.db.exists", side_effect=self._exists(False)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp(user=""))
            reassert_company_user_permission(self._emp(company=""))
        add.assert_not_called()

    def test_never_removes_a_permission(self):
        """Add-only: an area manager's hand-granted second company must survive."""
        with patch("frappe.db.exists", side_effect=self._exists(True)), patch(
            "frappe.permissions.remove_user_permission"
        ) as remove:
            reassert_company_user_permission(self._emp())
        remove.assert_not_called()
```

Update the import line at the top of that file from:

```python
from barakat.overrides.staff_roles import guard_role_preset
```

to:

```python
from barakat.overrides.staff_roles import (
    guard_role_preset,
    reassert_company_user_permission,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run (on a bench): `bench --site <site> run-tests --module barakat.overrides.test_staff_roles`
Expected: FAIL — `ImportError: cannot import name 'reassert_company_user_permission'`.

- [ ] **Step 3: Write minimal implementation**

In `barakat/overrides/staff_roles.py`, add this function immediately above `guard_role_preset`:

```python
def reassert_company_user_permission(doc, method=None):
	"""Ensure the Employee's linked user keeps their company User Permission.

	This is the tenant boundary. ERPNext creates it in
	`erpnext/setup/doctype/employee/employee.py::update_user_permissions`, but only
	when `user_id` or the `create_user_permission` checkbox CHANGES — and it DELETES
	both the Employee and the Company permission when that box is unticked. The
	checkbox's description says only "This will restrict user access to other employee
	records", so unticking it silently unscopes the user from every other company's
	data. Measured on BOM: a scoped user sees 0 of 190 POS invoices; an unscoped one
	sees all of them.

	Re-asserting here splits the two concerns the checkbox conflates: it keeps owning
	the own-employee-record restriction, we own the tenant restriction.

	ADD-ONLY. Never removes a permission: staff may legitimately span shops, and a
	second company granted by hand must survive.
	"""
	from frappe.permissions import add_user_permission

	preset = (doc.custom_role_preset or "").strip()
	if preset not in PERSONAS:
		return

	email = (doc.user_id or "").strip()
	company = (doc.company or "").strip()
	if not email or not company or email == "Administrator":
		return
	if not frappe.db.exists("User", email):
		return

	if frappe.db.exists(
		"User Permission", {"user": email, "allow": "Company", "for_value": company}
	):
		return

	add_user_permission("Company", company, email)
```

In `barakat/hooks.py`, change the `Employee` block so both events run the new hook too:

```python
	"Employee": {
		"validate": [
			"barakat.validations.validate_employee_pin",
			"barakat.overrides.staff_roles.guard_role_preset",
		],
		"after_insert": [
			"barakat.overrides.staff_roles.reassert_persona_roles",
			"barakat.overrides.staff_roles.reassert_company_user_permission",
		],
		"on_update": [
			"barakat.overrides.staff_roles.reassert_persona_roles",
			"barakat.overrides.staff_roles.reassert_company_user_permission",
		],
	},
```

- [ ] **Step 4: Run test to verify it passes**

Run (on a bench): `bench --site <site> run-tests --module barakat.overrides.test_staff_roles`
Expected: PASS — the 4 `GuardRolePreset` tests, the 2 `StaffManagerPerms` tests and the 5 new `ReassertCompanyUserPermission` tests.

Confirm the local pure suites are unaffected:
`/c/Python313/python -m unittest barakat.overrides.test_persona_guard barakat.test_custom_fields` → PASS (15 tests)
`/c/Python313/python -m py_compile barakat/overrides/staff_roles.py barakat/hooks.py` → no output

- [ ] **Step 5: Commit**

```bash
git add barakat/overrides/staff_roles.py barakat/hooks.py barakat/overrides/test_staff_roles.py
git commit -m "fix(scoping): re-assert the company User Permission on every Employee save"
```

---

### Task 3: Migration patch to normalise existing marker values

**Files:**
- Create: `barakat/patches/scope_customer_company_markers.py`
- Modify: `barakat/patches.txt`

**Interfaces:**
- Consumes: the `Customer.custom_company` / `Customer.custom_branch` fieldnames from Task 1.

- [ ] **Step 1: Write the patch**

Create `barakat/patches/scope_customer_company_markers.py`:

```python
"""Normalise Customer company/branch markers after the Data -> Link conversion.

Two reasons this must run:

1. A row whose marker matches no Company/Branch is now an invalid Link. It stays
   readable, but the next save of that Customer throws LinkValidationError. We do
   NOT blank such values — guessing a tenant is worse than a visible gap — we count
   and report them so the rollout has evidence.
2. A BLANK marker is visible to EVERYONE. Frappe emits
   `ifnull(field,'')='' or field in (...)` unless `apply_strict_user_permissions` is
   on, so an empty company defeats the whole boundary. We fill blanks only when the
   site has exactly one company, where the answer is unambiguous.

Idempotent: a second run matches everything and changes nothing.

See docs/superpowers/specs/2026-07-23-customer-company-scoping-design.md
"""

import frappe


def _normalise(fieldname, valid_values, fill_value):
	matched = filled = unmatched = 0
	unmatched_examples = []

	for row in frappe.get_all("Customer", fields=["name", fieldname]):
		value = (row.get(fieldname) or "").strip()

		if value and value in valid_values:
			matched += 1
		elif not value and fill_value:
			frappe.db.set_value(
				"Customer", row.name, fieldname, fill_value, update_modified=False
			)
			filled += 1
		elif value:
			unmatched += 1
			if len(unmatched_examples) < 5:
				unmatched_examples.append(f"{row.name}={value!r}")

	print(
		f"[barakat] Customer.{fieldname}: matched={matched} blanks_filled={filled} "
		f"unmatched={unmatched}"
	)
	if unmatched_examples:
		print(f"[barakat] Customer.{fieldname} unmatched examples: {unmatched_examples}")


def execute():
	companies = set(frappe.get_all("Company", pluck="name"))
	branches = set(frappe.get_all("Branch", pluck="name"))

	# Only safe to infer when there is exactly one company on the site.
	sole_company = next(iter(companies)) if len(companies) == 1 else None
	if not sole_company and companies:
		print(
			f"[barakat] {len(companies)} companies on this site - blank Customer "
			f"companies are left alone and reported, not guessed."
		)

	_normalise("custom_company", companies, sole_company)
	# Branch blanks are the norm and there is no Branch user permission in play,
	# so never fill them - only report values that match no Branch.
	_normalise("custom_branch", branches, None)
```

- [ ] **Step 2: Register the patch**

In `barakat/patches.txt`, append at the end (under `[post_model_sync]`, so it runs after the fixture sync has converted the fields):

```
barakat.patches.scope_customer_company_markers
```

- [ ] **Step 3: Verify it compiles**

Run: `/c/Python313/python -m py_compile barakat/patches/scope_customer_company_markers.py`
Expected: no output.

- [ ] **Step 4: Run on a bench and verify the leak is closed**

```bash
bench --site <site> migrate
```
Expected output includes a line like
`[barakat] Customer.custom_company: matched=2325 blanks_filled=1 unmatched=0`

Then re-measure as a company-scoped user (this is the acceptance test for the whole plan).
Write `/tmp/verify.py`:

```python
import frappe
frappe.set_user("staffbom2@gmail.com")
print("CUSTOMER total", frappe.db.count("Customer"), "visible", len(frappe.get_list("Customer", limit_page_length=0)))
print("ITEM total", frappe.db.count("Item"), "visible", len(frappe.get_list("Item", limit_page_length=0)))
```

Run: `bench --site <site> console < /tmp/verify.py`
Expected: `CUSTOMER total 2326 visible 2` or similar — **visible must be far below total**. Before this plan it was `visible 2326`. The `ITEM` line is the control and should stay `visible 1`.

Run migrate a second time and confirm the patch reports `blanks_filled=0` (idempotent).

- [ ] **Step 5: Commit**

```bash
git add barakat/patches/scope_customer_company_markers.py barakat/patches.txt
git commit -m "fix(scoping): patch to normalise Customer company/branch markers"
```

---

## Rollout

1. Push `dev`; promote per the normal merge flow when the user says.
2. Per live site: `sudo -u frappe git -C apps/barakat pull upstream <branch>` → `sudo -u frappe bench --site <site> migrate` (syncs the fixtures **and** runs the patch) → `sudo -u frappe bench restart`.
3. Keep each site's patch output (`matched / blanks_filled / unmatched`). Any non-zero `unmatched` needs a human decision before those Customers can be saved again.
4. No backfill command is needed — the Task 2 hook fixes permissions going forward, and every current staff user already has one.

## Self-review notes

- **Spec coverage:** §1 Customer.custom_company → Task 1; §2 Customer.custom_branch → Task 1; §3 Customer Group fixture → Task 1; §4 migration patch → Task 3; §5 company re-assert hook → Task 2; blank-visibility risk → Task 3 Step 1; testing matrix → Tasks 1–3. All spec sections mapped.
- **Known behaviour change:** after this lands, a BOM2 user sees ~2 customers instead of 2,326 at the ERPNext layer. That is the fix working. **No AP-visible change** — the proxy already filtered customers to the active company.
- **Watch during Task 3's bench run:** if `unmatched > 0` on any site, stop and report the values rather than blanking them; those rows will fail their next save until a human assigns a real company.
