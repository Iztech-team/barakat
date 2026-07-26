# Loyalty Tier-Name Uniqueness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a Loyalty Program from ever holding two tiers with the same name, repair any that already do, and make the POS survive one if it ever sees one.

**Architecture:** One rule expressed in four places. A Frappe-free Python module owns the comparison and rename logic; a `validate` hook rejects duplicates at save time; a patch renames exact duplicates already in the database; the AP shows the error inline on the offending row; the POS applies the same rename while pulling so a duplicate can never reach its local primary key.

**Tech Stack:** Frappe/ERPNext (Python 3.14, `unittest` + `FrappeTestCase`), React + zod + react-hook-form (AP), Bun + Drizzle + SQLite + `bun:test` (POS).

## Global Constraints

- **Never touch `petromall.iztech.net`.** It shares the prod bench but is not a Barakat site. Never run `bench --site all`. Every site loop filters it: `sudo -u frappe ls sites | grep -v -E 'assets|apps|common|\.json|\.log|\.txt' | grep -v petromall`.
- **Every repo starts on `dev`.** Never edit while on `test` or `main`. Promotion is merge-only: `dev` → `test` → `main`.
- **Version bumps happen on `dev`, before promoting.** AP deploys on push; promoting without bumping makes prod report a version that is not what is running.
- **Two comparisons, never mixed.** Save-time checks compare *normalized* names (trimmed, case-folded). Repair paths compare *exact* strings. See Task 1 for why.
- **Repair renames, never deletes.** A duplicate row carries its own `min_spent` and `collection_factor`; dropping it silently changes what customers earn.
- **barakat uses tab indentation** in `validations.py` and `hooks.py`, **4 spaces** in `barakat/patches/*.py` and `test_*.py`. Match the file you are editing.
- Spec: `docs/superpowers/specs/2026-07-26-loyalty-tier-name-uniqueness-design.md`.

## File Structure

| File | Repo | Responsibility |
| --- | --- | --- |
| `barakat/loyalty_tier_names.py` | barakat | **Create.** Frappe-free comparison + rename logic. Sole owner of the rule. |
| `barakat/test_loyalty_tier_names.py` | barakat | **Create.** Pure unit tests, runnable on Windows. |
| `barakat/validations.py` | barakat | **Modify.** Add the `validate` hook function. |
| `barakat/hooks.py` | barakat | **Modify.** Wire the hook to `Loyalty Program`. |
| `barakat/test_validations.py` | barakat | **Modify.** Bench tests for the hook. |
| `barakat/patches/dedupe_loyalty_tier_names.py` | barakat | **Create.** Repair patch. |
| `barakat/patches.txt` | barakat | **Modify.** Register the patch. |
| `barakat/test_dedupe_loyalty_tier_names.py` | barakat | **Create.** Bench test for the patch. |
| `src/schemas/pages/customers/loyalty-program.ts` | AP | **Modify.** New zod rule + message key. |
| `src/hooks/pages/_app/customers/loyalty-manipulate.ts` | AP | **Modify.** Wire the translated message. |
| `src/i18n/locales/{en,ar,he}.json` | AP | **Modify.** Three new strings. |
| `src/bun/sync/pull-loyalty-programs.ts` | POS | **Modify.** Rename duplicates while building tiers. |
| `src/bun/sync/pull-loyalty-programs.spec.ts` | POS | **Modify.** Test the duplicate case. |

Repo paths: barakat `C:\Users\IzTech-OTbaileh\Desktop\bar\barakat`, AP `C:\Users\IzTech-OTbaileh\Desktop\barakat-repos\admin_panel_barakat`, POS `C:\Users\IzTech-OTbaileh\Desktop\electrobun-pos`.

---

### Task 1: The rule, as Frappe-free logic

Both the validation and the patch need this, and it is the only part that can be tested without a bench. It follows the existing `barakat/patches/_uom_scope_logic.py` + `barakat/test_uom_scope_logic.py` pattern exactly.

**Files:**
- Create: `barakat/loyalty_tier_names.py`
- Test: `barakat/test_loyalty_tier_names.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `normalize_tier_name(name) -> str`
  - `first_duplicate_tier_name(names) -> str | None` — the original (unfolded) string of the first row whose normalized key already appeared; `None` if all distinct. Blank names are skipped.
  - `resolve_exact_duplicates(names) -> dict[int, str]` — index → new name, for exact repeats only. Indices absent from the dict are unchanged.

- [ ] **Step 1: Write the failing test**

Create `barakat/test_loyalty_tier_names.py`:

```python
"""Pure, Frappe-free tests for the loyalty tier-name rules.

Runs locally:  python -m unittest barakat.test_loyalty_tier_names
"""

import unittest

from barakat.loyalty_tier_names import (
    first_duplicate_tier_name,
    normalize_tier_name,
    resolve_exact_duplicates,
)


class NormalizeTierName(unittest.TestCase):
    def test_trims_and_folds_case(self):
        self.assertEqual(normalize_tier_name("  VIP  "), "vip")

    def test_arabic_passes_through(self):
        self.assertEqual(normalize_tier_name(" شريحة "), "شريحة")

    def test_none_is_blank(self):
        self.assertEqual(normalize_tier_name(None), "")


class FirstDuplicateTierName(unittest.TestCase):
    def test_distinct_names_are_clean(self):
        self.assertIsNone(first_duplicate_tier_name(["Bronze", "Gold", "شريحة"]))

    def test_exact_repeat_is_reported(self):
        self.assertEqual(first_duplicate_tier_name(["vip vip", "vip vip"]), "vip vip")

    def test_case_and_space_repeat_is_reported(self):
        self.assertEqual(first_duplicate_tier_name(["VIP", " vip "]), " vip ")

    def test_blank_names_are_skipped(self):
        self.assertIsNone(first_duplicate_tier_name(["", "", None]))


class ResolveExactDuplicates(unittest.TestCase):
    def test_distinct_names_rename_nothing(self):
        self.assertEqual(resolve_exact_duplicates(["Bronze", "Gold"]), {})

    def test_case_difference_is_left_alone(self):
        self.assertEqual(resolve_exact_duplicates(["VIP", "vip"]), {})

    def test_second_copy_gets_suffix_two(self):
        self.assertEqual(
            resolve_exact_duplicates(["vip vip", "vip vip"]), {1: "vip vip (2)"}
        )

    def test_third_copy_keeps_counting(self):
        self.assertEqual(
            resolve_exact_duplicates(["a", "a", "a"]), {1: "a (2)", 2: "a (3)"}
        )

    def test_skips_a_suffix_already_used_later_in_the_program(self):
        # `a (2)` exists further down the list, so the rename must jump past it.
        self.assertEqual(resolve_exact_duplicates(["a", "a", "a (2)"]), {1: "a (3)"})

    def test_first_occurrence_is_never_renamed(self):
        self.assertNotIn(0, resolve_exact_duplicates(["a", "a"]))
```

- [ ] **Step 2: Run the test to verify it fails**

From the barakat repo root:

```bash
python -m unittest barakat.test_loyalty_tier_names
```

Expected: `ModuleNotFoundError: No module named 'barakat.loyalty_tier_names'`.

- [ ] **Step 3: Write the minimal implementation**

Create `barakat/loyalty_tier_names.py` (4-space indentation):

```python
"""Pure tier-name rules for Loyalty Program collection rules — no Frappe imports.

Two different comparisons, deliberately.

`normalize_tier_name` (strip + casefold) is the SAVE-TIME rule. It rejects `VIP`
sitting next to `vip ` — names every human reads as one tier — at the moment
someone is typing them.

Exact string equality is the REPAIR rule, used by the patch and mirrored by the
POS. Only an exact repeat actually breaks anything: the POS keys its local tier
table on (site_url, program, tier_name). Repair paths run unattended against live
configuration, so they touch only rows that genuinely break something, and never
rename a tier on a program that works.
"""


def normalize_tier_name(name):
    """Save-time comparison key: surrounding space and case are noise."""
    return (name or "").strip().casefold()


def first_duplicate_tier_name(names):
    """First name whose normalized key already appeared, else None.

    Returns the ORIGINAL string so the error message quotes what the user typed
    rather than the folded key. Blank names are skipped — the child doctype marks
    `tier_name` as `reqd`, so Frappe already rejects those, and flagging them here
    would put two errors on one row.
    """
    seen = set()
    for name in names:
        key = normalize_tier_name(name)
        if not key:
            continue
        if key in seen:
            return name
        seen.add(key)
    return None


def resolve_exact_duplicates(names):
    """Map index -> new name for every exact repeat. Untouched rows are absent.

    Keeps the first occurrence and renames each later one to `<name> (2)`, `(3)`,
    ... incrementing until the candidate matches no other name in the program.

    `taken` is seeded with EVERY name up front, not filled in as we go. A name
    further down the list can already be `a (2)`, and handing that string to an
    earlier row would create the very collision we are removing.
    """
    taken = set(names)
    seen = set()
    renames = {}
    for index, name in enumerate(names):
        if name not in seen:
            seen.add(name)
            continue
        counter = 2
        while f"{name} ({counter})" in taken:
            counter += 1
        renamed = f"{name} ({counter})"
        taken.add(renamed)
        seen.add(renamed)
        renames[index] = renamed
    return renames
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python -m unittest barakat.test_loyalty_tier_names
```

Expected: `OK`, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add barakat/loyalty_tier_names.py barakat/test_loyalty_tier_names.py
git commit -m "feat(loyalty): tier-name comparison and rename rules"
```

---

### Task 2: Reject duplicates at save time

**Files:**
- Modify: `barakat/validations.py`
- Modify: `barakat/hooks.py`
- Test: `barakat/test_validations.py`

**Interfaces:**
- Consumes: `first_duplicate_tier_name` from Task 1.
- Produces: `validate_loyalty_program_tier_names(doc, method)` — raises `frappe.ValidationError` on a duplicate, returns `None` otherwise.

- [ ] **Step 1: Write the failing test**

Append to `barakat/test_validations.py` (4-space indentation). Add the import to the existing `from barakat.validations import ...` line so it reads:

```python
from barakat.validations import (
    SALARY_ADVANCE_FIELD,
    validate_loyalty_program_tier_names,
    validate_pos_profile_accounts,
)
```

Then append the test class at the end of the file:

```python
class LoyaltyProgramTierNames(FrappeTestCase):
    """The rule that keeps a program's tier names distinct.

    Duplicate tier names are legal in ERPNext and fatal to the POS, whose local
    tier table is keyed on (site_url, program, tier_name).
    """

    def _doc(self, *names):
        return frappe._dict(
            {"collection_rules": [frappe._dict({"tier_name": n}) for n in names]}
        )

    def test_distinct_names_save(self):
        validate_loyalty_program_tier_names(self._doc("Bronze", "Gold"), "validate")

    def test_exact_duplicate_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            validate_loyalty_program_tier_names(
                self._doc("vip vip", "vip vip"), "validate"
            )

    def test_case_or_space_only_difference_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            validate_loyalty_program_tier_names(self._doc("VIP", " vip "), "validate")

    def test_blank_name_does_not_trip_this_rule(self):
        validate_loyalty_program_tier_names(self._doc("", ""), "validate")

    def test_program_with_no_tiers_is_fine(self):
        validate_loyalty_program_tier_names(frappe._dict({}), "validate")
```

- [ ] **Step 2: Confirm the test cannot pass yet**

This test imports `frappe`, so it cannot run on the Windows dev box, and it must NOT be run
by SSH-ing to the test bench: that box has the `test` branch checked out, and pulling `dev`
onto it would merge unpromoted code into the server's tracked branch. **Bench verification
for this task is the controller's job**, in one pass after Task 3.

Instead, confirm locally that the test targets code that does not exist yet:

```bash
python -c "import ast,sys; src=open('barakat/validations.py',encoding='utf-8').read(); print('validate_loyalty_program_tier_names' in src)"
```

Expected: `False` before Step 3, `True` after.

- [ ] **Step 3: Write the minimal implementation**

In `barakat/validations.py`, add to the imports at the top:

```python
from barakat.loyalty_tier_names import first_duplicate_tier_name
```

Then add the function (tab indentation, matching the rest of this file):

```python
def validate_loyalty_program_tier_names(doc, method):
	"""No two tiers in one program may share a name.

	ERPNext allows it; the POS cannot survive it. Its local `loyalty_tiers` table
	is keyed on (site_url, program, tier_name) and every program is written in one
	transaction, so a single duplicate rolls back the whole set and — being a
	SQLite error rather than a network one — never retries. One mistyped tier name
	takes a till's entire loyalty sync down permanently.

	Compared trimmed and case-insensitively: `VIP` beside `vip ` is a typo, not a
	second tier.
	"""
	names = [row.tier_name for row in (doc.get("collection_rules") or [])]
	duplicate = first_duplicate_tier_name(names)
	if not duplicate:
		return
	frappe.throw(
		title=_("Duplicate Tier Name"),
		msg=_(
			"This loyalty program already has a tier named <b>{0}</b>. "
			"Give each tier its own name."
		).format(duplicate),
	)
```

In `barakat/hooks.py`, add a new entry to `doc_events`, after the `"Customer"` block:

```python
	"Loyalty Program": {
		"validate": "barakat.validations.validate_loyalty_program_tier_names",
	},
```

- [ ] **Step 4: Check what can be checked locally**

The bench run happens in the Bench Verification step after Task 3. Locally, confirm the
module still parses and the new symbol is importable in isolation:

```bash
python -c "import ast; ast.parse(open('barakat/validations.py',encoding='utf-8').read()); ast.parse(open('barakat/hooks.py',encoding='utf-8').read()); print('parse ok')"
python -m unittest barakat.test_loyalty_tier_names
```

Expected: `parse ok`, and Task 1's 13 tests still `OK` (this task must not disturb them).

- [ ] **Step 5: Commit**

```bash
git add barakat/validations.py barakat/hooks.py barakat/test_validations.py
git commit -m "feat(loyalty): reject duplicate tier names on save"
```

---

### Task 3: Repair duplicates already in the database

**Files:**
- Create: `barakat/patches/dedupe_loyalty_tier_names.py`
- Modify: `barakat/patches.txt`
- Test: `barakat/test_dedupe_loyalty_tier_names.py`

**Interfaces:**
- Consumes: `resolve_exact_duplicates` from Task 1.
- Produces: `execute()` — the standard Frappe patch entry point. Idempotent.

The test inserts child rows directly with SQL rather than building a Loyalty Program document. That is not a shortcut: after Task 2 ships, the document API *cannot* create a duplicate, so a direct insert is the only way to produce the state this patch exists to repair. It also avoids needing a company and a chart of accounts.

- [ ] **Step 1: Write the failing test**

Create `barakat/test_dedupe_loyalty_tier_names.py` (4-space indentation):

```python
"""On-bench test for the duplicate tier-name repair patch.

Run on a site:
    bench --site <site> run-tests --module barakat.test_dedupe_loyalty_tier_names
Not runnable on the Windows dev box (imports `frappe`).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.patches.dedupe_loyalty_tier_names import execute

PARENT = "ZZ Patch Test Program"


class DedupeLoyaltyTierNames(FrappeTestCase):
    def setUp(self):
        self._clear()

    def tearDown(self):
        self._clear()

    def _clear(self):
        frappe.db.sql(
            "delete from `tabLoyalty Program Collection` where parent = %s", PARENT
        )
        frappe.db.commit()

    def _insert(self, idx, tier_name, min_spent, collection_factor):
        # Inserted as a document, not raw SQL: the child table still has Frappe's
        # non-null bookkeeping columns (owner, creation, modified, modified_by,
        # docstatus), and get_doc fills them. A hand-written INSERT that omits
        # them fails on MariaDB for reasons unrelated to what this test asserts.
        frappe.get_doc(
            {
                "doctype": "Loyalty Program Collection",
                "parent": PARENT,
                "parenttype": "Loyalty Program",
                "parentfield": "collection_rules",
                "idx": idx,
                "tier_name": tier_name,
                "min_spent": min_spent,
                "collection_factor": collection_factor,
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()

    def _rows(self):
        return frappe.db.sql(
            """select tier_name, min_spent, collection_factor
               from `tabLoyalty Program Collection`
               where parent = %s order by idx""",
            PARENT,
            as_dict=True,
        )

    def test_duplicate_is_renamed_and_keeps_its_numbers(self):
        self._insert(1, "vip vip", 0, 656567)
        self._insert(2, "vip vip", 2000, 50)

        execute()

        rows = self._rows()
        self.assertEqual([r.tier_name for r in rows], ["vip vip", "vip vip (2)"])
        # The renamed row keeps its own threshold and factor — a rename must not
        # change what customers earn.
        self.assertEqual(rows[1].min_spent, 2000)
        self.assertEqual(rows[1].collection_factor, 50)

    def test_second_run_changes_nothing(self):
        self._insert(1, "vip vip", 0, 10)
        self._insert(2, "vip vip", 2000, 5)

        execute()
        after_first = self._rows()
        execute()

        self.assertEqual(self._rows(), after_first)

    def test_distinct_names_are_left_alone(self):
        self._insert(1, "Bronze", 0, 10)
        self._insert(2, "Gold", 2000, 5)

        execute()

        self.assertEqual([r.tier_name for r in self._rows()], ["Bronze", "Gold"])
```

- [ ] **Step 2: Confirm the test cannot pass yet**

Same rule as Task 2: this test imports `frappe`, and the bench run is the controller's job
in the Bench Verification step below. Do not SSH to the test box. Confirm locally:

```bash
python -c "import os; print(os.path.exists('barakat/patches/dedupe_loyalty_tier_names.py'))"
```

Expected: `False` before Step 3, `True` after.

- [ ] **Step 3: Write the minimal implementation**

Create `barakat/patches/dedupe_loyalty_tier_names.py` (4-space indentation):

```python
"""Rename exact-duplicate tier names inside a Loyalty Program.

Two collection rules with the same `tier_name` are legal in ERPNext and fatal to
the POS: it keys its local tier table on (site_url, program, tier_name), writes
every program in one transaction, and treats the resulting UNIQUE failure as
non-retryable — so one duplicate takes a till's whole loyalty sync down for good.

Renames rather than deletes. The duplicate row carries its own `min_spent` and
`collection_factor`; dropping it would quietly change what customers earn.

Idempotent: after a run there are no exact repeats left, so a second run renames
nothing. Expected to be a no-op on every current site — it exists for duplicates
created before the save-time guard shipped, and for sites restored from an older
backup.
"""

import frappe

from barakat.loyalty_tier_names import resolve_exact_duplicates


def execute():
    if not frappe.db.table_exists("Loyalty Program Collection"):
        return  # fresh site: the child table syncs after patches run

    parents = frappe.db.sql_list(
        """select distinct parent from `tabLoyalty Program Collection`
           where parenttype = 'Loyalty Program'"""
    )
    for parent in parents:
        rows = frappe.db.sql(
            """select name, tier_name from `tabLoyalty Program Collection`
               where parent = %s and parenttype = 'Loyalty Program'
               order by idx""",
            parent,
            as_dict=True,
        )
        for index, new_name in resolve_exact_duplicates(
            [r.tier_name for r in rows]
        ).items():
            row = rows[index]
            # Written straight to the child row: re-saving the parent would run
            # the Loyalty Program validation mid-migration, and `modified` is left
            # alone because the POS re-fetches every program on each pull anyway.
            frappe.db.set_value(
                "Loyalty Program Collection",
                row.name,
                "tier_name",
                new_name,
                update_modified=False,
            )
            print(f"{parent}: {row.tier_name!r} -> {new_name!r}")
    frappe.db.commit()
```

Register it in `barakat/patches.txt` — append as the last line under `[post_model_sync]`:

```
barakat.patches.dedupe_loyalty_tier_names
```

- [ ] **Step 4: Check what can be checked locally**

```bash
python -c "import ast; ast.parse(open('barakat/patches/dedupe_loyalty_tier_names.py',encoding='utf-8').read()); print('parse ok')"
python -c "print(open('barakat/patches.txt',encoding='utf-8').read().rstrip().endswith('barakat.patches.dedupe_loyalty_tier_names'))"
```

Expected: `parse ok`, then `True`.

- [ ] **Step 5: Commit**

```bash
git add barakat/patches/dedupe_loyalty_tier_names.py barakat/patches.txt barakat/test_dedupe_loyalty_tier_names.py
git commit -m "feat(loyalty): patch renaming duplicate tier names"
```

---

### Bench Verification (controller-run, after Task 3)

Tasks 2 and 3 write tests that need a real bench. Neither implementer runs them: the test
EC2 has the `test` branch checked out, so the only correct way to get code onto it is the
normal promotion, not a `dev` pull. The controller does this once, for both modules.

- [ ] **Step 1: Promote barakat `dev` to `test` and push**

```bash
git checkout test && git merge dev && git push && git checkout dev
```

Safe for this repo: nothing deploys on push — the bench only changes when someone pulls.

- [ ] **Step 2: Pull `test` on the test box and restart**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 "cd /home/frappe/erp_project && sudo -u frappe git -C apps/barakat pull upstream test && sudo -u frappe /home/frappe/.local/bin/bench restart"
```

- [ ] **Step 3: Run both new test modules**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 "cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench --site qa-test.test.barakat.iztech.net run-tests --module barakat.test_validations && sudo -u frappe /home/frappe/.local/bin/bench --site qa-test.test.barakat.iztech.net run-tests --module barakat.test_dedupe_loyalty_tier_names"
```

Expected: `OK` from both. `test_validations` must still pass its pre-existing POS Profile
cases, not just the new tier-name ones. Any failure here is a fix round against whichever
task owns the failing module.

---

### Task 4: Inline error in the Admin Panel

Repo: `C:\Users\IzTech-OTbaileh\Desktop\barakat-repos\admin_panel_barakat`, branch `dev`.

**Files:**
- Modify: `src/schemas/pages/customers/loyalty-program.ts`
- Modify: `src/hooks/pages/_app/customers/loyalty-manipulate.ts`
- Modify: `src/i18n/locales/en.json`, `src/i18n/locales/ar.json`, `src/i18n/locales/he.json`

**Interfaces:**
- Consumes: nothing from earlier tasks — this is the client-side half of the same rule.
- Produces: message key `loyalty.validationDuplicateTierName`; `LoyaltyProgramSchemaMessages.duplicateTierName`.

This repo has no test runner, so verification is a typecheck/lint pass plus a manual check on the form.

- [ ] **Step 1: Add the message key to the schema's message interface**

In `src/schemas/pages/customers/loyalty-program.ts`, add one line to `LoyaltyProgramSchemaMessages`, directly after `duplicateMinSpent`:

```ts
  duplicateMinSpent: string;
  duplicateTierName: string;
  toDateAfterFrom: string;
```

- [ ] **Step 2: Add the rule to the existing superRefine**

Same file. Inside `.superRefine(...)`, inside the existing `if (vals.tiers.length > 0) {` block, immediately after the `mins.forEach(...)` duplicate-minSpent loop and before its closing `}`:

```ts
        // Two tiers with the same name are legal in ERPNext and fatal to the POS:
        // it keys its local tier table on (site, program, tier name), so the
        // second row collides and takes that till's whole loyalty sync down.
        // Compared trimmed and case-insensitively — `VIP` next to `vip ` reads as
        // one tier to everyone except the database. Flag the second occurrence,
        // on the row itself, for the same reason the minSpent rule does.
        const seenNames = new Set<string>();
        vals.tiers.forEach((tier, index) => {
          const key = tier.tierName.trim().toLowerCase();
          if (!key) return;
          if (seenNames.has(key)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['tiers', index, 'tierName'],
              message: msgs.duplicateTierName
            });
          }
          seenNames.add(key);
        });
```

- [ ] **Step 3: Add the placeholder to the default schema**

Same file, in the `loyaltyProgramSchema` export, after `duplicateMinSpent: 'invalid',`:

```ts
  duplicateTierName: 'invalid',
```

- [ ] **Step 4: Wire the translated message**

In `src/hooks/pages/_app/customers/loyalty-manipulate.ts`, inside `useSchema()`, after the `duplicateMinSpent` line:

```ts
    duplicateTierName: t('loyalty.validationDuplicateTierName'),
```

- [ ] **Step 5: Add the three strings**

Add each directly after the existing `validationDuplicateMinSpent` entry in its file, keeping the surrounding indentation.

`src/i18n/locales/en.json`:

```json
    "validationDuplicateTierName": "Each tier must have a different name",
```

`src/i18n/locales/ar.json`:

```json
    "validationDuplicateTierName": "يجب أن يكون لكل شريحة اسم مختلف",
```

`src/i18n/locales/he.json`:

```json
    "validationDuplicateTierName": "לכל דרגה חייב להיות שם שונה",
```

- [ ] **Step 6: Verify**

```bash
bun run tsc && bun run lint && bun run prettier:check
```

Expected: all three clean. If `prettier:check` complains about the files you touched, run `bun run prettier:fix` on them.

- [ ] **Step 7: Check it in the browser**

Start the proxy then the AP (proxy first — an AP that loads but "does not work" is almost always a proxy that is not running). Check first, and do not start a second copy of either:

```bash
netstat -ano | findstr ":8099 " | findstr LISTENING
```

Open http://localhost:3000 (never `127.0.0.1:3000` — vite binds IPv6 `localhost` only), go to a multi-tier loyalty program, add a second tier with a name that already exists, and confirm a red error appears on that row and Save is blocked. Then change the case of one letter and confirm it is still blocked.

- [ ] **Step 8: Commit**

```bash
git add src/schemas/pages/customers/loyalty-program.ts src/hooks/pages/_app/customers/loyalty-manipulate.ts src/i18n/locales/en.json src/i18n/locales/ar.json src/i18n/locales/he.json
git commit -m "feat(loyalty): block duplicate tier names in the program form"
```

---

### Task 5: Make the POS survive a duplicate

Repo: `C:\Users\IzTech-OTbaileh\Desktop\electrobun-pos`, branch `dev`.

**Files:**
- Modify: `src/bun/sync/pull-loyalty-programs.ts`
- Test: `src/bun/sync/pull-loyalty-programs.spec.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new exports. `pullLoyaltyProgramsOnce()` keeps its `{ pulledCount, hasMore }` shape.

The rename must produce the *same* names as `resolve_exact_duplicates` in Task 1, so a till that syncs before the patch runs and one that syncs after agree. That is why `taken` is seeded with every name up front rather than filled in as the loop goes: with `["a", "a", "a (2)"]`, an incremental set would hand `a (2)` to index 1 and shift everything, while seeding matches the server and yields `a (3)`.

- [ ] **Step 1: Write the failing test**

In `src/bun/sync/pull-loyalty-programs.spec.ts`, extend the imports at the top:

```ts
import { afterEach, beforeEach, describe, expect, it, mock } from "bun:test";
import { getDb } from "../db/database";
import { loyaltyTiers } from "../db/schema";
import { clearTestDb, makeFreshTestDb } from "../_test-helpers/test-db";
```

Then add this test inside the existing `describe("pullLoyaltyProgramsOnce", ...)` block:

```ts
	it("renames a duplicate tier name instead of failing the whole pull", async () => {
		// ERPNext allows two collection rules with the same name. The local
		// loyalty_tiers PK is (site_url, program, tier_name), and every program is
		// written in ONE transaction — so before this fix a single duplicate threw
		// UNIQUE constraint failed and rolled back every program, for ever.
		requestMock
			.mockResolvedValueOnce({
				data: [{ name: "Multi Tier", modified: "2026-01-01 00:00:00" }],
			})
			.mockResolvedValueOnce({
				data: {
					name: "Multi Tier",
					loyalty_program_type: "Multiple Tier Program",
					conversion_factor: 1,
					expiry_duration: 365,
					from_date: "2026-01-01",
					to_date: null,
					auto_opt_in: 1,
					customer_group: null,
					customer_territory: null,
					company: "Barakat",
					expense_account: "Loyalty - B",
					cost_center: null,
					modified: "2026-01-01 00:00:00",
					collection_rules: [
						{ tier_name: "vip vip", min_spent: 0, collection_factor: 10 },
						{ tier_name: "vip vip", min_spent: 2000, collection_factor: 5 },
					],
				},
			});
		const { pullLoyaltyProgramsOnce } = await import("./pull-loyalty-programs");
		const res = await pullLoyaltyProgramsOnce();

		expect(res.pulledCount).toBe(1);
		const rows = getDb().select().from(loyaltyTiers).all();
		expect(rows.map((r) => r.tierName).sort()).toEqual([
			"vip vip",
			"vip vip (2)",
		]);
		// Both thresholds survive: tiers are read back ordered by minSpent to pick a
		// customer's collection factor, so dropping a row would change what people earn.
		expect(rows.find((r) => r.tierName === "vip vip (2)")?.minSpent).toBe(2000);
		expect(
			rows.find((r) => r.tierName === "vip vip (2)")?.collectionFactor,
		).toBe(5);
	});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
bun test src/bun/sync/pull-loyalty-programs.spec.ts
```

Expected: FAIL with a SQLite `UNIQUE constraint failed: loyalty_tiers.site_url, loyalty_tiers.program, loyalty_tiers.tier_name`. That failure *is* the bug — confirm you see it before fixing.

- [ ] **Step 3: Write the minimal implementation**

In `src/bun/sync/pull-loyalty-programs.ts`, add this function above `pullLoyaltyProgramsOnce`:

```ts
/**
 * Give every collection rule a name unique within its program.
 *
 * ERPNext permits two rules with the same `tier_name`; the local `loyalty_tiers`
 * table is keyed on (site_url, program, tier_name) and every program is written
 * in ONE transaction, so a single duplicate throws UNIQUE constraint failed and
 * rolls back the whole set. A SQLite error is not retryable either, so the scope
 * goes red and stays red on every later sync — one mistyped tier name takes a
 * till's entire loyalty sync down permanently.
 *
 * Renames rather than drops: tiers are read back ordered by `minSpent` to choose
 * a customer's collection factor, so discarding a row would quietly change what
 * customers earn. The name plays no part in that choice.
 *
 * `taken` is seeded with EVERY name up front — matching resolve_exact_duplicates
 * in the barakat app — so a till that syncs before the repair patch runs and one
 * that syncs after end up with identical names. Filling the set incrementally
 * would hand out `a (2)` to a row whose program already has an `a (2)` further
 * down the list.
 */
function withUniqueTierNames(rules: RawTier[]): { rule: RawTier; name: string }[] {
	const taken = new Set(rules.map((r) => r.tier_name));
	const seen = new Set<string>();
	return rules.map((rule) => {
		if (!seen.has(rule.tier_name)) {
			seen.add(rule.tier_name);
			return { rule, name: rule.tier_name };
		}
		let counter = 2;
		while (taken.has(`${rule.tier_name} (${counter})`)) counter += 1;
		const renamed = `${rule.tier_name} (${counter})`;
		taken.add(renamed);
		seen.add(renamed);
		return { rule, name: renamed };
	});
}
```

Then replace the tier loop inside `pullLoyaltyProgramsOnce`. Change:

```ts
		for (const t of p.collection_rules ?? []) {
			tiers.push({
				program: p.name,
				tierName: t.tier_name,
				minSpent: t.min_spent ?? 0,
				collectionFactor: t.collection_factor ?? 0,
			});
		}
```

to:

```ts
		for (const { rule, name } of withUniqueTierNames(p.collection_rules ?? [])) {
			tiers.push({
				program: p.name,
				tierName: name,
				minSpent: rule.min_spent ?? 0,
				collectionFactor: rule.collection_factor ?? 0,
			});
		}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
bun test src/bun/sync/pull-loyalty-programs.spec.ts
```

Expected: PASS, 2 tests. The pre-existing "upserts programs and their tiers" test must still pass.

- [ ] **Step 5: Run the full verification**

```bash
bun run verify
```

Expected: typecheck, lint, format check and the whole test suite all clean.

- [ ] **Step 6: Commit**

```bash
git add src/bun/sync/pull-loyalty-programs.ts src/bun/sync/pull-loyalty-programs.spec.ts
git commit -m "fix(loyalty): rename duplicate tier names instead of failing the pull"
```

---

### Task 6: Version, promote, deploy, verify

Nothing ships until this task. Do the repos in this order — barakat first, so the guard and the repair are in place before tills start pulling.

**Files:**
- Modify: `barakat/__init__.py` (`1.3.1` → `1.4.0`)
- Modify: AP `package.json` (`1.4.0` → `1.5.0`)
- Modify: POS `electrobun.config.ts` (`2.3.0` → `2.3.1`)

**Interfaces:**
- Consumes: Tasks 1–5, all committed on `dev` in their repos.
- Produces: three deployed components; `dev`, `test` and `main` identical in each repo, local and remote.

- [ ] **Step 1: Bump all three versions on `dev`**

barakat — `barakat/__init__.py`: `__version__ = "1.4.0"` (minor: new validation rule and patch).
AP — `package.json`: `"version": "1.5.0"` (minor: a new save-time rule users see).
POS — `electrobun.config.ts`: `version: "2.3.1"` (patch: a crash fix, no new behaviour).

Commit in each repo, adding **only** the version file. Never `git commit -am` here: the
AP working tree carries unrelated modified files (`shifts-table/columns.tsx`,
`date-locale.ts`, `settings-erpnext.tsx`) that belong to someone else's work, and `-am`
would sweep them into a release commit and deploy them.

```bash
# barakat
git add barakat/__init__.py && git commit -m "chore: bump barakat to 1.4.0 (loyalty tier-name uniqueness)"
# AP
git add package.json && git commit -m "chore: bump AP to 1.5.0 (loyalty tier-name uniqueness)"
# POS
git add electrobun.config.ts && git commit -m "chore: bump POS to 2.3.1 (loyalty tier-name uniqueness)"
```

Before promoting the AP, confirm those three unrelated files are still uncommitted and
untouched: `git status --porcelain` should show exactly `M` on those three and nothing else.

- [ ] **Step 2: Push `dev` in all three repos**

```bash
git push origin dev
```

Expected: three clean pushes. Nothing deploys yet except the AP's dev site, which is known broken and not used.

- [ ] **Step 3: Promote barakat and deploy the test bench**

```bash
git checkout test && git merge dev && git push && git checkout dev
```

Then on the test box — note `git pull upstream test`, because the test EC2 tracks the `test` branch:

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35
```

```bash
cd /home/frappe/erp_project && sudo -u frappe git -C apps/barakat pull upstream test
```

Migrate each site by name, petromall excluded. On this box the list is `fatima.test.barakat.iztech.net`, `master.35.158.120.8.nip.io`, `qa-test.test.barakat.iztech.net`, `qa-training.test.barakat.iztech.net` — re-derive it rather than trusting this list:

```bash
for s in $(sudo -u frappe ls sites | grep -v -E 'assets|apps|common|\.json|\.log|\.txt' | grep -v petromall); do echo "== $s"; sudo -u frappe /home/frappe/.local/bin/bench --site $s migrate; done
```

```bash
sudo -u frappe /home/frappe/.local/bin/bench restart
```

Expected: the patch prints nothing (no duplicates exist), and `bench version` reports `barakat 1.4.0`.

- [ ] **Step 4: Promote barakat to prod and deploy the prod bench**

```bash
git checkout main && git merge test && git push && git checkout dev
git tag -a v1.4.0 -m "barakat v1.4.0" && git push origin v1.4.0
```

On the prod box — `git pull upstream main`, because the prod EC2 tracks `main`:

```bash
ssh -i ~/.ssh/barakat-prod.pem ubuntu@52.59.163.201
```

```bash
cd /home/frappe/erp_project && sudo -u frappe git -C apps/barakat pull upstream main
for s in $(sudo -u frappe ls sites | grep -v -E 'assets|apps|common|\.json|\.log|\.txt' | grep -v petromall); do echo "== $s"; sudo -u frappe /home/frappe/.local/bin/bench --site $s migrate; done
sudo -u frappe /home/frappe/.local/bin/bench restart
```

**Check the site list before running the loop.** It must contain `barakat.iztech.net`, `bm.iztech.net`, `bom.iztech.net`, `niveen1.iztech.net`, `test.iztech.net` and must NOT contain `petromall.iztech.net`.

- [ ] **Step 5: Promote the AP**

Pushing is deploying for this repo — the bump from Step 1 must already be in.

```bash
git checkout test && git merge dev && git push
git checkout main && git merge test && git push && git checkout dev
git tag -a v1.5.0 -m "AP v1.5.0" && git push origin v1.5.0
```

Verify: open https://test.barakat.iztech.net/sign-in/ and https://console.barakat.iztech.net/ and confirm the sidebar footer reads `1.5.0`.

- [ ] **Step 6: Build and release the POS**

Build and release must target the same env — `publish-update.ts` refuses a mismatch, and the guard exists because the wrong ERPNext URL ships silently.

```bash
git checkout test && git merge dev && git push
bun run build:test && bun run release:test
```

```bash
git checkout main && git merge test && git push
bun run build:prod && bun run release:prod
git tag -a v2.3.1 -m "POS v2.3.1" && git push origin v2.3.1
git checkout dev
```

- [ ] **Step 7: Verify the original failure is gone**

On `qa-test.test.barakat.iztech.net`, in the ERPNext desk or the AP, try to add a second tier named exactly like an existing one on any multi-tier program. Expected: rejected, with the tier name quoted back.

Then open Settings → Sync data on a test till and press sync. Expected: **Loyalty programs** shows a row count, not "Sync failed".

- [ ] **Step 8: Confirm every repo is clean and aligned**

In each of the three repos:

```bash
git status --porcelain
git log --oneline -1 dev test main
```

Expected: empty status, and `dev`, `test`, `main` all pointing at the same commit.

---

## Self-Review

**Spec coverage** — every section maps to a task: the shared rule → Task 1; Layer 1 (AP) → Task 4; Layer 2 (validation) → Task 2; Layer 3 (patch) → Task 3; Layer 4 (POS) → Task 5; Testing → the test steps in Tasks 1–5; Rollout → Task 6. The petromall exclusion appears in Global Constraints and again in both migration steps.

**Placeholders** — none. Every code step carries the actual code; every run step carries the actual command and its expected output.

**Type consistency** — `first_duplicate_tier_name`, `normalize_tier_name` and `resolve_exact_duplicates` are defined in Task 1 and used under those exact names in Tasks 2 and 3. `duplicateTierName` and `loyalty.validationDuplicateTierName` are consistent across the four AP files in Task 4. `withUniqueTierNames` returns `{ rule, name }` and is destructured as `{ rule, name }` in Task 5.

**One deliberate divergence, stated in both places:** the save-time rule compares normalized names, the repair paths compare exact strings. Task 1's docstring and the Global Constraints both explain why, so an engineer reading a single task does not "fix" the inconsistency.
