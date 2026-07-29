"""Tests for the localized chart of accounts.

Frappe-free on purpose — `barakat_chart` imports nothing from frappe, so the
whole chart can be checked with plain unittest:

	python -m unittest barakat.chart_of_accounts.test_barakat_chart
"""

import unittest

from barakat.chart_of_accounts.barakat_chart import (
	METADATA_FIELDS,
	STRUCTURE,
	account_names,
	build_chart,
	translate,
)
from barakat.chart_of_accounts.names import NAMES

ROOTS = (
	"Application of Funds (Assets)",
	"Source of Funds (Liabilities)",
	"Income",
	"Expenses",
	"Equity",
)


def english_names(node=None):
	"""Every account name in STRUCTURE, English."""
	names = []
	for key, value in (node if node is not None else STRUCTURE).items():
		if key in METADATA_FIELDS:
			continue
		names.append(key)
		names.extend(english_names(value))
	return names


class TestNames(unittest.TestCase):
	def test_every_account_has_arabic_and_hebrew(self):
		missing = [n for n in english_names() if n not in NAMES]
		self.assertEqual(missing, [], f"no translation for: {missing}")

	def test_no_translation_is_blank(self):
		for english, pair in NAMES.items():
			self.assertTrue(pair.get("ar", "").strip(), f"Arabic missing for {english}")
			self.assertTrue(pair.get("he", "").strip(), f"Hebrew missing for {english}")

	def test_the_table_has_no_entries_the_chart_never_uses(self):
		# A stale name is dead weight that reads like a supported account.
		extra = sorted(set(NAMES) - set(english_names()))
		self.assertEqual(extra, [], f"in NAMES but not in the chart: {extra}")


class TestUniqueness(unittest.TestCase):
	"""An Account docname is `account_name - abbr`, unique per company."""

	def _assert_unique(self, lang):
		names = account_names(lang)
		dupes = sorted({n for n in names if names.count(n) > 1})
		self.assertEqual(dupes, [], f"duplicate {lang} names would fail to insert: {dupes}")

	def test_arabic_names_are_unique(self):
		self._assert_unique("ar")

	def test_hebrew_names_are_unique(self):
		self._assert_unique("he")

	def test_english_names_are_unique(self):
		self._assert_unique("en")


class TestBuildChart(unittest.TestCase):
	def test_roots_are_translated(self):
		chart = build_chart("ar", "ILS")
		self.assertIn("الأصول", chart)
		self.assertIn("الالتزامات", chart)
		self.assertNotIn("Application of Funds (Assets)", chart)

	def test_root_type_survives_translation(self):
		chart = build_chart("ar", "ILS")
		self.assertEqual(chart["الأصول"]["root_type"], "Asset")
		self.assertEqual(chart["الإيرادات"]["root_type"], "Income")

	def test_nested_accounts_are_translated(self):
		chart = build_chart("ar", "ILS")
		receivables = chart["الأصول"]["الأصول المتداولة"]["الذمم المدينة"]
		self.assertIn("العملاء", receivables)
		self.assertEqual(receivables["العملاء"]["account_type"], "Receivable")

	def test_account_types_are_preserved_everywhere(self):
		# The whole ledger depends on these; a translation must never move one.
		chart = build_chart("he", "ILS")
		stock = chart["הוצאות"]["הוצאות ישירות"]["הוצאות מלאי"]
		self.assertEqual(stock["התאמת מלאי"]["account_type"], "Stock Adjustment")
		self.assertEqual(stock["עלות המכר"]["account_type"], "Cost of Goods Sold")

	def test_english_is_the_structure_unchanged(self):
		self.assertEqual(sorted(account_names("en")), sorted(english_names()))

	def test_currency_is_stamped_on_every_node(self):
		# create_charts reads account_currency FROM THE CHART when a custom
		# chart is passed. Miss one and that account is created with none.
		chart = build_chart("ar", "USD")

		def check(node, path):
			self.assertEqual(node.get("account_currency"), "USD", f"no currency at {path}")
			for key, value in node.items():
				if key not in METADATA_FIELDS:
					check(value, f"{path} > {key}")

		for name, node in chart.items():
			check(node, name)

	def test_a_missing_currency_is_refused(self):
		with self.assertRaises(ValueError):
			build_chart("ar", "")

	def test_an_unknown_language_is_refused(self):
		with self.assertRaises(ValueError):
			build_chart("fr", "ILS")

	def test_an_untranslated_account_keeps_its_english_name(self):
		# Better one English line than an account missing from the books.
		self.assertEqual(translate("Not In The Table", "ar"), "Not In The Table")


class TestStructure(unittest.TestCase):
	def test_the_five_roots_are_present_and_typed(self):
		self.assertEqual(sorted(STRUCTURE), sorted(ROOTS))
		for root in ROOTS:
			self.assertIn("root_type", STRUCTURE[root], f"{root} has no root_type")

	def test_the_accounts_barakat_depends_on_exist(self):
		names = english_names()
		for required in (
			"Debtors",
			"Creditors",
			"Cash",
			"Stock In Hand",
			"Stock Adjustment",
			"Stock Received But Not Billed",
			"Round Off",
			"Write Off",
			"Cost of Goods Sold",
			"Sales",
			"Payroll Payable",
		):
			self.assertIn(required, names)

	def test_vat_is_left_to_erpnext(self):
		"""ERPNext's country tax setup creates VAT itself, a few seconds after
		the chart, and links THAT account to the Sales Taxes and Charges
		Template. Shipping our own produced two VAT accounts under the same
		group, only one of them wired to anything.
		"""
		self.assertNotIn("VAT", english_names())


if __name__ == "__main__":
	unittest.main()
