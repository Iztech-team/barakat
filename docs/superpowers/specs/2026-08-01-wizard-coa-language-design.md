# Setup wizard: chart of accounts in the site language — design

**Date:** 2026-08-01
**Repos touched:** `barakat` (Frappe app) only
**Status:** approved, not yet implemented

## Problem

A shop created through the AP gets its chart of accounts in the owner's language: the
proxy sends `custom_barakat_coa_language` on the Company insert, and
`BarakatCompany.create_default_accounts` builds the Arabic or Hebrew chart
(`barakat/overrides/company.py`, `barakat/chart_of_accounts/`).

The **first** company on a site is not created that way. It is created by ERPNext's setup
wizard, which has no such field, so the language arrives empty and the company falls
through to ERPNext's English chart — even when the site itself was set up in Arabic.

This cannot be repaired afterwards. An Account's docname is minted once, at insert, and
`Account.allow_rename` is 0; `barakat/api/chart_of_accounts.py` can rename the five roots
only while the company has zero GL entries. Company creation is the one moment the chart's
language can be chosen.

## Where the chart is actually built

Confirmed on the test bench (erpnext `799d6d1`, version-16 line), so a later reader does not
have to re-derive it:

1. `frappe.desk.page.setup_wizard.setup_wizard.get_setup_stages` puts frappe's own
   **"Updating global settings"** stage first, then appends each installed app's
   `setup_wizard_stages` hooks in `frappe.get_installed_apps()` order.
2. That first stage runs `update_global_settings` → `update_system_settings(args)`, which
   saves **`System Settings.language`** as the Language docname for the language chosen in
   the wizard (`ar`, `he`, `en`, …).
3. ERPNext's stage 2, "Setting up company", runs
   `erpnext/setup/setup_wizard/operations/install_fixtures.py::install_company`, which
   inserts the Company with `create_chart_of_accounts_based_on: "Standard Template"` and
   `chart_of_accounts: args.chart_of_accounts`.
4. `Company.on_update` sees the company has no Accounts yet and calls
   `create_default_accounts()` → `create_charts()`. **That is where the chart is born**,
   and `hooks.py` already replaces that method with ours.

Nothing creates accounts before this. A fresh site has zero Accounts until a Company
exists; `bench new-site --install-app erpnext` creates none.

## Goals

- A site set up in Arabic or Hebrew gets its first company's books in that language,
  with no new question in the wizard.
- English and every other language behave exactly as they do today.
- No ERPNext or Frappe file is modified, and no new `hooks.py` entry is added.

## Non-goals

- Choosing the books' language separately from the site's language. A site that wants an
  Arabic desk with English books is not served by this change; that combination has never
  been asked for, and the AP path still allows anything.
- Companies created outside the wizard with no language set — from the desk, a fixture, or
  a script. They keep ERPNext's English chart. Scope is the wizard only, so the rule can
  never surprise someone adding a second company by hand.
- Anything retroactive. Sites already set up keep their English books.
- Any change to `petromall.iztech.net`, which shares the prod bench but is not a Barakat
  site.

## The change

One method on the class we already own, in `barakat/overrides/company.py`:

```python
def before_insert(self):
    if frappe.flags.in_setup_wizard and not self.get(COA_LANGUAGE_FIELD):
        self.set(COA_LANGUAGE_FIELD, _site_chart_language())
```

Neither `Company` nor `NestedSet` defines `before_insert`, so this stands alone — there is
no super call to keep in step, unlike `create_default_accounts`.

**`frappe.flags.in_setup_wizard`** is set by `process_setup_stages` for the whole run and
cleared when it ends, so it is true for exactly the wizard's company and nothing else.

**`_site_chart_language()`** reads `System Settings.language`, takes the part before any
`-`, lower-cases it, and returns it only if it is one of `TRANSLATED_LANGUAGES` —
otherwise the empty string. So `ar` → Arabic, `ar-SA` → Arabic, `he` → Hebrew, and `en`,
`en-US`, an unset value, or a language we have no chart for all leave the field empty and
reach ERPNext's English chart unchanged.

Reading it inside the same transaction is safe: stage 1 saved System Settings, and stages
deliberately do not commit — frappe commits once after all of them — so the write is
visible on the same connection.

The helper is kept frappe-free (it takes the code as an argument) so it can be unit tested
without a site, matching `barakat/chart_of_accounts/test_barakat_chart.py`.

## Seams that were rejected

Recorded so they are not re-investigated:

- **Adding our charts to the wizard's existing "Chart of Accounts" dropdown.**
  `get_charts_for_country` only scans ERPNext's own `verified/` folder, matching files by
  country-code prefix. Adding ours means writing JSON inside the erpnext app, which an
  upgrade wipes.
- **A `setup_wizard_stages` hook in barakat.** Stages run in installed-app order — frappe,
  then erpnext, then barakat — so ours would run after the company already exists and the
  chart is already English.
- **Passing a sentinel through `args.chart_of_accounts`.** It does reach the Company
  verbatim, but it needs a JS slide override and puts a value ERPNext does not recognise
  into a field it also feeds to `sync_financial_report_templates`. More moving parts than
  the language field we already have.

## Testing

- **Unit, frappe-free:** the code→language mapping. `ar` → `ar`, `ar-SA` → `ar`,
  `he` → `he`, `en` → `""`, `en-US` → `""`, `""` → `""`, `None` → `""`.
- **Unit, with a site:** insert a Company with `frappe.flags.in_setup_wizard` set and the
  field empty, and assert it is filled; insert one with the flag unset and assert it is
  not; insert one that already carries a language and assert it is left alone.
- **Manual, on the test bench:** create a fresh site, run the wizard in Arabic, then check
  the first company's accounts have Arabic docnames (`المبيعات - <abbr>`) and Arabic roots.
  Repeat in English and confirm the chart is byte-for-byte what it is today.
- **Regression:** an AP create-shop still honours the language the proxy sends.

## Rollout

- Ships in the `barakat` app alone. `custom_barakat_coa_language` already ships as a
  fixture (`barakat/fixtures/custom_field.json`), so there is no new field and no
  migration.
- `dev` is at `3.0.0`; this is a **minor** bump — a new site behaves differently.
- Only effective on a bench running the v2 "born translated" chart. Prod must be carrying
  that before this means anything there.
- No effect on any existing site: the rule fires at Company insert, and every site that
  exists already has its company.

## Risks

- If ERPNext later gives `Company` a `before_insert`, ours silently shadows it. Cheap to
  check on an ERPNext upgrade, and noted here for that reason.
- `update_system_settings` returns early when the wizard sends no country, leaving
  `System Settings.language` at its default. The books then come out English, which is
  the current behaviour — the failure mode is the status quo, not a broken chart.
