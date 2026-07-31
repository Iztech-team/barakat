"""Create a new company's chart of accounts in the owner's language.

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
