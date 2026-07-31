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
