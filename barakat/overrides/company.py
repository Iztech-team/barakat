"""Company overrides: the chart-of-accounts language, and the cash Mode of Payment.

Two unrelated things live here because both are `Company` behaviour ERPNext gets
wrong for a multi-tenant site. See `set_mode_of_payment_account` for the second.

## Why an override rather than a rename afterwards

An Account's docname is minted once, when the account is inserted, as
`account_name - abbr`, and can never be changed: `Account.allow_rename` is 0, so
even `frappe.rename_doc` refuses ("Account not allowed to be renamed"). Renaming
after creation therefore translates only what the owner READS — every screen
that prints an id keeps showing English, and ERPNext additionally refuses to
touch the five root accounts by any route at all.

Company creation is the one moment the ids can be chosen. `create_charts` names
each account from the chart's own keys, so handing it an Arabic chart produces
`المبيعات - BOM36` outright: no rename step, no root special case, and no
English left anywhere to leak back.

## Why the method is copied rather than extended

ERPNext's `create_default_accounts` does three things — build the chart, then
point `default_receivable_account` and `default_payable_account` at the first
Receivable and Payable leaves. Only the first differs for us, but the chart call
is in the middle, so there is no seam to hook. The two `db_set` calls below are
therefore a deliberate copy of ERPNext's, and must be kept in step with it. If a
future ERPNext adds a fourth step here, this override silently stops doing it —
that is the cost of this approach and the reason for this note.

A company with no language set, or set to English, falls straight through to
ERPNext's own behaviour.
"""

import frappe
from erpnext.setup.doctype.company.company import Company

from barakat.chart_of_accounts.barakat_chart import build_chart
from barakat.chart_of_accounts.site_language import TRANSLATED_LANGUAGES, language_for_new_company

# Set by the proxy in the Company insert payload. Ships as a fixture custom
# field; a site that has not migrated simply has no value here, and the company
# then gets ERPNext's English chart.
COA_LANGUAGE_FIELD = "custom_barakat_coa_language"

# What makes a Mode of Payment belong to one shop. Ships as a fixture custom field
# ("Mode of Payment-custom_company" in hooks.py) because ERPNext's own Mode of
# Payment is global — it has no company link of its own.
MODE_COMPANY_FIELD = "custom_company"


class BarakatCompany(Company):
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

	def create_default_accounts(self):
		lang = (self.get(COA_LANGUAGE_FIELD) or "").strip().lower()
		if lang not in TRANSLATED_LANGUAGES:
			return super().create_default_accounts()

		from erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts import (
			create_charts,
		)

		try:
			chart = build_chart(lang, self.default_currency)
		except Exception:
			# A malformed chart must not stop a shop being created. Fall back to
			# ERPNext's English chart, which is strictly better than no accounts.
			frappe.log_error(
				f"Could not build the {lang} chart for {self.name}; using ERPNext's default.",
				"barakat.chart_of_accounts",
			)
			return super().create_default_accounts()

		frappe.local.flags.ignore_root_company_validation = True
		create_charts(self.name, custom_chart=chart)

		# Copied from ERPNext's create_default_accounts — see the module docstring.
		self.db_set(
			"default_receivable_account",
			frappe.db.get_value(
				"Account", {"company": self.name, "account_type": "Receivable", "is_group": 0}
			),
		)
		self.db_set(
			"default_payable_account",
			frappe.db.get_value(
				"Account", {"company": self.name, "account_type": "Payable", "is_group": 0}
			),
		)

	def set_mode_of_payment_account(self):
		"""Only ever write this company's cash account into this company's OWN mode.

		## The bug this closes

		ERPNext's version (setup/doctype/company/company.py) is:

		    cash = frappe.db.get_value("Mode of Payment", {"type": "Cash"}, "name")

		No company filter, no `order_by` — it takes whichever Cash-type mode the
		database happens to return first, then appends THIS company's cash account
		to it. That is safe in stock ERPNext, where "Cash" is a single global mode.
		It is not safe here: the admin panel gives every shop its own mode, named
		`<mode> - <Company>` and tagged `custom_company`, so the query picks blind
		among all of them. Measured on qa-test 2026-08-03 with 10 Cash-type modes,
		it returned `Cash 2 E2E - E2E Shop` — one shop's mode, about to receive
		another shop's account.

		Worse, this runs from `on_update`, not `after_insert` (company.py:362), so
		it fires on EVERY company save — a rename, a currency change, the setup
		walkthrough confirming accounts, a migration touching companies.

		## Why a stray row is not cosmetic

		Every staff login carries one User Permission pinning it to its company, and
		Frappe's document-level check walks child-table links too. So the moment a
		mode's `accounts` table names a second company, that mode becomes unreadable
		to every persona of every shop — the tenant owner, who is not pinned, still
		reads it. That is ticket 0001-595: recording a supplier payment reads the
		chosen mode, got a PermissionError, and the admin panel reported it as
		"you do not have permission to use this payment method".

		## Why "do nothing" is the right fallback

		A company with no mode of its own gets no row anywhere. Writing into the
		shared "Cash" instead would only move the pollution: the setup readiness
		gate counts modes by `custom_company == company`, so a row on the shared
		mode satisfies nothing, and it would break the shared mode for every
		persona on the site. Creating the shop's payment modes belongs to the
		admin panel's Company Accounts walkthrough, which is where a human chooses
		the account.

		A site whose custom field has not been created yet (never migrated) has no
		per-company modes either, so ERPNext's own behaviour is still correct there
		and is left alone.
		"""
		if not frappe.db.has_column("Mode of Payment", MODE_COMPANY_FIELD):
			return super().set_mode_of_payment_account()

		if not self.default_cash_account:
			return

		cash = frappe.db.get_value(
			"Mode of Payment",
			{"type": "Cash", MODE_COMPANY_FIELD: self.name},
			"name",
			order_by="creation asc",
		)
		if not cash:
			return

		if frappe.db.get_value("Mode of Payment Account", {"company": self.name, "parent": cash}):
			return

		mode_of_payment = frappe.get_doc("Mode of Payment", cash, for_update=True)
		mode_of_payment.append(
			"accounts", {"company": self.name, "default_account": self.default_cash_account}
		)
		mode_of_payment.save(ignore_permissions=True)
