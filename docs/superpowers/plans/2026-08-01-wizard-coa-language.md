# Setup wizard chart language — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A site set up through ERPNext's wizard in Arabic or Hebrew gets its first company's chart of accounts in that language, with no new question in the wizard.

**Architecture:** All the branching lives in a new frappe-free module, `barakat/chart_of_accounts/site_language.py`, so it can be unit-tested with plain `unittest` and no bench — the same pattern as `barakat/overrides/treeview.py::visible_rows`. `BarakatCompany.before_insert` is four lines of glue that reads three values off frappe and stamps the result on `custom_barakat_coa_language`, which the existing `create_default_accounts` override already acts on.

**Tech Stack:** Python 3.14, Frappe/ERPNext v16, plain `unittest`, ruff via pre-commit.

**Spec:** [`docs/superpowers/specs/2026-08-01-wizard-coa-language-design.md`](../specs/2026-08-01-wizard-coa-language-design.md)

## Global Constraints

- Work happens on `dev`. Nothing is promoted to `test` or `main` without the user's explicit go-ahead — Task 3 is gated on it.
- Bump `__version__` in `barakat/__init__.py` from `3.0.0` to `3.1.0` **in Task 2, before any promotion**. A new site behaves differently, so this is a minor bump.
- Never run anything against `petromall.iztech.net`. The verification site is `qa-test.test.barakat.iztech.net` on the **test** box (`ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35`).
- New shared logic must import nothing from frappe, so `python -m unittest` runs it on this laptop. `frappe` is **not** importable locally — a module that imports it cannot be unit-tested here.
- Python style: tabs for indentation, double quotes, ruff `line-length = 110`. Pre-commit runs ruff and ruff-format; if a hook rewrites a file during a commit, `git add` it again and re-commit.
- English is never given a chart. A company with no language falls through to ERPNext's own English chart, exactly as today.

---

### Task 1: The language decision, frappe-free

**Files:**
- Create: `barakat/chart_of_accounts/site_language.py`
- Create: `barakat/chart_of_accounts/test_site_language.py`
- Modify: `barakat/overrides/company.py:41` (delete the local `TRANSLATED_LANGUAGES` and import it from the new module instead)

**Interfaces:**
- Consumes: `SUPPORTED_LANGUAGES` from `barakat.chart_of_accounts.barakat_chart` (test only, as a cross-check).
- Produces:
  - `TRANSLATED_LANGUAGES: tuple[str, ...]` — `("ar", "he")`, the single definition from here on.
  - `chart_language(site_language: str | None) -> str` — a Frappe language code to a chart language, or `""`.
  - `language_for_new_company(in_setup_wizard: bool, current_language: str | None, site_language: str | None) -> str` — the language to stamp on a Company being inserted, or `""` to leave it alone.

- [ ] **Step 1: Write the failing test**

Create `barakat/chart_of_accounts/test_site_language.py`:

```python
"""Tests for the chart language a company gets when nobody chose one.

Frappe-free on purpose — `site_language` imports nothing from frappe, so the
whole decision can be checked with plain unittest:

	python -m unittest barakat.chart_of_accounts.test_site_language
"""

import unittest

from barakat.chart_of_accounts.barakat_chart import SUPPORTED_LANGUAGES
from barakat.chart_of_accounts.site_language import (
	TRANSLATED_LANGUAGES,
	chart_language,
	language_for_new_company,
)


class TestChartLanguage(unittest.TestCase):
	def test_arabic_and_hebrew_have_charts(self):
		self.assertEqual(chart_language("ar"), "ar")
		self.assertEqual(chart_language("he"), "he")

	def test_a_region_suffix_is_dropped(self):
		self.assertEqual(chart_language("ar-SA"), "ar")
		self.assertEqual(chart_language("he-IL"), "he")

	def test_case_and_surrounding_space_do_not_matter(self):
		self.assertEqual(chart_language("  AR  "), "ar")

	def test_english_is_left_to_erpnext(self):
		self.assertEqual(chart_language("en"), "")
		self.assertEqual(chart_language("en-US"), "")

	def test_missing_or_unknown_languages_get_nothing(self):
		for value in ("", "   ", None, "fr", "zh-TW"):
			with self.subTest(value=value):
				self.assertEqual(chart_language(value), "")

	def test_every_translated_language_can_actually_be_built(self):
		for lang in TRANSLATED_LANGUAGES:
			with self.subTest(lang=lang):
				self.assertEqual(chart_language(lang), lang)
				self.assertIn(lang, SUPPORTED_LANGUAGES)


class TestLanguageForNewCompany(unittest.TestCase):
	def test_the_wizard_company_takes_the_site_language(self):
		self.assertEqual(language_for_new_company(True, "", "ar"), "ar")
		self.assertEqual(language_for_new_company(True, "   ", "he"), "he")

	def test_nothing_is_filled_in_outside_the_wizard(self):
		self.assertEqual(language_for_new_company(False, "", "ar"), "")

	def test_a_language_already_chosen_is_never_overwritten(self):
		self.assertEqual(language_for_new_company(True, "he", "ar"), "")

	def test_an_english_site_is_left_to_erpnext(self):
		self.assertEqual(language_for_new_company(True, "", "en"), "")

	def test_missing_inputs_leave_the_company_alone(self):
		self.assertEqual(language_for_new_company(True, None, None), "")


if __name__ == "__main__":
	unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run from the repo root (`C:\Users\IzTech-OTbaileh\Desktop\bar\barakat`):

```bash
python -m unittest barakat.chart_of_accounts.test_site_language -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'barakat.chart_of_accounts.site_language'`.

- [ ] **Step 3: Write the implementation**

Create `barakat/chart_of_accounts/site_language.py`:

```python
"""Which chart of accounts language a company gets when nobody chose one.

ERPNext's setup wizard has no chart-language field, so the first company on a
site arrives with `custom_barakat_coa_language` empty and falls through to the
English chart — even on a site that was set up in Arabic. An Account's name is
minted at insert and cannot be changed afterwards, so that first company is the
only chance to get its books right.

The wizard does ask for a language, and frappe's "Updating global settings"
stage saves it to `System Settings.language` before ERPNext's company stage
runs. This module turns that code into a chart language.

Frappe-free on purpose: the caller reads the three inputs off frappe and does
nothing else, so the whole decision can be checked with plain unittest:

	python -m unittest barakat.chart_of_accounts.test_site_language

See docs/superpowers/specs/2026-08-01-wizard-coa-language-design.md.
"""

# The languages we hold a chart for. English is deliberately absent: a company
# with no language set gets ERPNext's own English chart, which is what we want.
TRANSLATED_LANGUAGES = ("ar", "he")


def chart_language(site_language):
	"""The chart language for a Frappe language code, or "" if we have no chart.

	A code may carry a region suffix (`ar-SA`, `he-IL`) and the chart does not
	vary by region, so only the part before the dash is considered.
	"""
	code = (site_language or "").strip().lower().split("-")[0]
	return code if code in TRANSLATED_LANGUAGES else ""


def language_for_new_company(in_setup_wizard, current_language, site_language):
	"""The language to stamp on a Company being inserted, or "" to leave it alone.

	Three ways to get "", each deliberate:

	  * not the setup wizard — the AP always sends a language of its own, and a
	    company added by hand from the desk should not silently change language
	    because of a site-wide setting;
	  * a language was already chosen — never overwrite a caller's choice;
	  * the site's language has no chart — English and everything else keep
	    ERPNext's behaviour.
	"""
	if not in_setup_wizard:
		return ""
	if (current_language or "").strip():
		return ""
	return chart_language(site_language)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python -m unittest barakat.chart_of_accounts.test_site_language -v
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Run the existing chart tests to confirm nothing moved under them**

```bash
python -m unittest barakat.chart_of_accounts.test_barakat_chart -v
```

Expected: PASS, same count as before this change.

- [ ] **Step 6: Point `company.py` at the single definition**

In `barakat/overrides/company.py`, replace the local constant with an import. Delete this line:

```python
TRANSLATED_LANGUAGES = ("ar", "he")
```

and extend the existing import block so it reads:

```python
from barakat.chart_of_accounts.barakat_chart import build_chart
from barakat.chart_of_accounts.site_language import TRANSLATED_LANGUAGES
```

Nothing else in that file changes — `create_default_accounts` keeps using `TRANSLATED_LANGUAGES` exactly as it does now.

- [ ] **Step 7: Check the file still parses (frappe is not importable here, so this is the only local check)**

```bash
python -m py_compile barakat/overrides/company.py
```

Expected: no output, exit 0.

- [ ] **Step 8: Commit**

```bash
git add barakat/chart_of_accounts/site_language.py barakat/chart_of_accounts/test_site_language.py barakat/overrides/company.py
git commit -m "feat(coa): decide a new company's chart language from the site language"
```

---

### Task 2: Stamp it on the wizard's company

**Files:**
- Modify: `barakat/overrides/company.py` (add `before_insert` to `BarakatCompany`)
- Modify: `barakat/__init__.py:5` (`3.0.0` → `3.1.0`)

**Interfaces:**
- Consumes: `language_for_new_company` from Task 1, and `COA_LANGUAGE_FIELD` already defined at the top of `company.py`.
- Produces: nothing new. `create_default_accounts` in the same class already reads the field.

- [ ] **Step 1: Add the import**

In `barakat/overrides/company.py`, extend the import added in Task 1 so it reads:

```python
from barakat.chart_of_accounts.site_language import TRANSLATED_LANGUAGES, language_for_new_company
```

- [ ] **Step 2: Add `before_insert` to `BarakatCompany`, directly above `create_default_accounts`**

```python
	def before_insert(self):
		"""Give the setup wizard's company the language the site was set up in.

		The wizard has no chart-language field, so without this the first company
		on an Arabic site gets English books — and an Account's name is minted at
		insert, so there is no fixing it afterwards.

		`frappe.flags.in_setup_wizard` is set by frappe for the whole wizard run
		and cleared when it ends, so this fires for exactly that one company.
		`System Settings.language` was saved by the wizard's first stage, which
		runs before ERPNext creates the company; stages do not commit
		individually, so the write is visible on this connection.

		Neither `Company` nor `NestedSet` defines `before_insert`, so there is no
		super call to keep in step. If a future ERPNext adds one, this silently
		shadows it.
		"""
		language = language_for_new_company(
			bool(frappe.flags.in_setup_wizard),
			self.get(COA_LANGUAGE_FIELD),
			frappe.db.get_single_value("System Settings", "language"),
		)
		if language:
			self.set(COA_LANGUAGE_FIELD, language)
```

- [ ] **Step 3: Check it parses and the frappe-free suite is still green**

```bash
python -m py_compile barakat/overrides/company.py
```

Expected: no output, exit 0.

```bash
python -m unittest barakat.chart_of_accounts.test_site_language barakat.chart_of_accounts.test_barakat_chart
```

Expected: PASS.

Note: no local test exercises `before_insert` itself — it needs frappe, which is not installed on this machine. Task 1 covers every branch of the decision it delegates to; Task 3 is the proof that the glue is wired correctly.

- [ ] **Step 4: Bump the version**

In `barakat/__init__.py`, change:

```python
__version__ = "3.0.0"
```

to:

```python
__version__ = "3.1.0"
```

- [ ] **Step 5: Commit**

```bash
git add barakat/overrides/company.py barakat/__init__.py
git commit -m "feat(coa): build the setup wizard's chart in the site language

Bumps barakat to 3.1.0 — a new site's first company now gets Arabic or
Hebrew books when the site was set up in that language."
```

- [ ] **Step 6: Push `dev`**

`upstream` is `Iztech-team/barakat`, the repo the benches pull from. `origin` is a personal fork and pushing there deploys nothing.

```bash
git push upstream dev
```

---

### Task 3: Prove it on the test bench

**Gate:** this promotes `dev` to `test` and pulls it on the test server. **Ask the user for the go-ahead before starting this task.** Nothing here touches prod.

**Files:** none in the repo — this is a branch promotion plus a bench check.

- [ ] **Step 1: Promote to `test` and push**

```bash
git checkout test && git merge dev && git push upstream test && git checkout dev
```

- [ ] **Step 2: Pull and restart on the test box**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'cd /home/frappe/erp_project && sudo -u frappe git -C apps/barakat pull upstream test && sudo -u frappe /home/frappe/.local/bin/bench restart'
```

Expected: the pull reports the two new commits; `bench restart` finishes without error. No `bench migrate` is needed — `custom_barakat_coa_language` already ships as a fixture and no schema changed.

- [ ] **Step 3: Confirm the new version is live**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench version | grep barakat'
```

Expected: `barakat 3.1.0 test (<sha>)`.

- [ ] **Step 4: Write the Arabic check script onto the box**

This inserts a throwaway company with the wizard flag set, prints what it got, and always restores the site language and deletes the company — even if the insert raises.

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'cat > /tmp/wizard_lang_check.py <<"PY"
import frappe

SITE_LANG = "ar"
COMPANY = "Wizard Lang Check"
ABBR = "WLC1"

old_lang = frappe.db.get_single_value("System Settings", "language")
try:
	frappe.db.set_single_value("System Settings", "language", SITE_LANG)
	frappe.flags.in_setup_wizard = True
	company = frappe.get_doc({
		"doctype": "Company",
		"company_name": COMPANY,
		"abbr": ABBR,
		"default_currency": frappe.db.get_single_value("System Settings", "currency") or "ILS",
		"country": frappe.db.get_single_value("System Settings", "country") or "Israel",
	}).insert()
	print("LANGUAGE STAMPED:", repr(company.custom_barakat_coa_language))
	print("ACCOUNTS:", frappe.db.count("Account", {"company": company.name}))
	for row in frappe.get_all(
		"Account",
		filters={"company": company.name, "parent_account": ["in", ["", None]]},
		fields=["name", "account_name"],
	):
		print("ROOT:", row.name, "|", row.account_name)
finally:
	frappe.flags.in_setup_wizard = False
	frappe.db.set_single_value("System Settings", "language", old_lang)
	if frappe.db.exists("Company", COMPANY):
		frappe.delete_doc("Company", COMPANY, force=True, ignore_permissions=True)
	frappe.db.commit()
	print("CLEANED UP, language restored to", repr(old_lang))
PY'
```

- [ ] **Step 5: Run it against the QA site**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench --site qa-test.test.barakat.iztech.net console < /tmp/wizard_lang_check.py'
```

Expected:

```
LANGUAGE STAMPED: 'ar'
ACCOUNTS: 95
ROOT: الأصول - WLC1 | الأصول
ROOT: المصاريف - WLC1 | المصاريف
ROOT: الإيرادات - WLC1 | الإيرادات
ROOT: الالتزامات - WLC1 | الالتزامات
ROOT: حقوق الملكية - WLC1 | حقوق الملكية
CLEANED UP, language restored to 'en'
```

The docnames — not just the names — must be Arabic. That is the whole point of building the chart at insert. The five roots are what matter here; check them exactly.

`ACCOUNTS` is our 94 plus the `VAT` that ERPNext's country tax setup creates a few seconds later, so 95 — but it depends on whether that country has a tax template, so treat 94 or 95 as fine and an Arabic root as non-negotiable.

If the console swallows piped input, open it interactively (`bench --site qa-test.test.barakat.iztech.net console`) and paste the file's contents instead.

- [ ] **Step 6: Run the English negative check**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'sed -i "s/^SITE_LANG = \"ar\"/SITE_LANG = \"en\"/; s/^ABBR = \"WLC1\"/ABBR = \"WLC2\"/" /tmp/wizard_lang_check.py && cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench --site qa-test.test.barakat.iztech.net console < /tmp/wizard_lang_check.py'
```

Expected: `LANGUAGE STAMPED: ''`, 95 accounts, and English roots (`Application of Funds (Assets) - WLC2`, …) — ERPNext's own chart, unchanged. 95 rather than 94 because ERPNext's country tax setup adds its own `VAT`.

- [ ] **Step 7: Confirm the box is clean**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'rm -f /tmp/wizard_lang_check.py && cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench --site qa-test.test.barakat.iztech.net console <<"PY"
import frappe
print("leftovers:", frappe.get_all("Company", filters={"company_name": ["like", "Wizard Lang%"]}, pluck="name"))
print("language:", frappe.db.get_single_value("System Settings", "language"))
PY'
```

Expected: `leftovers: []` and the site's original language.

---

### Task 4 (optional): One real wizard run

Only worth doing if the user wants end-to-end proof rather than the code-path proof from Task 3. It needs a throwaway site and about ten minutes.

- [ ] **Step 1: Create a scratch site on the test box**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench new-site wizard-ar.test.barakat.iztech.net --install-app erpnext --install-app barakat'
```

- [ ] **Step 2: Run the wizard in Arabic**

Open the site, pick **العربية** as the language on the first slide, fill in the company, and finish.

- [ ] **Step 3: Check the books**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench --site wizard-ar.test.barakat.iztech.net console <<"PY"
import frappe
company = frappe.get_all("Company", pluck="name")[0]
print("company:", company, "| language:", repr(frappe.db.get_value("Company", company, "custom_barakat_coa_language")))
for row in frappe.get_all("Account", filters={"company": company, "parent_account": ["in", ["", None]]}, fields=["name"]):
	print("ROOT:", row.name)
PY'
```

Expected: `language: 'ar'` and Arabic root docnames.

- [ ] **Step 4: Drop the scratch site**

```bash
ssh -i ~/.ssh/barakat-test.pem ubuntu@52.59.253.35 'cd /home/frappe/erp_project && sudo -u frappe /home/frappe/.local/bin/bench drop-site wizard-ar.test.barakat.iztech.net --force'
```

---

## Not in this plan

- Promotion to `main` / the prod bench. That is a separate call by the user, and prod must be carrying the v2 "born translated" chart first.
- Any change to the AP or the proxy. Neither is involved: the proxy already sends the language explicitly on every shop it creates.
