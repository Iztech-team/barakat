"""Validation tests for the root-account renamer.

Frappe-free on purpose, like barakat/test_custom_fields.py: `clean_names` is
the whole input-validation surface of a privileged endpoint, so it is worth
testing without needing a site. It lives in `_root_name_validation` precisely
so this import does not drag in frappe.

	python -m unittest barakat.api.test_chart_of_accounts
"""

import json
import unittest

from barakat.api._root_name_validation import MAX_ACCOUNT_NAME, clean_names


class TestCleanNames(unittest.TestCase):
	def test_accepts_a_json_string(self):
		self.assertEqual(
			clean_names(json.dumps({"Expenses": "المصاريف"})),
			{"Expenses": "المصاريف"},
		)

	def test_accepts_a_dict(self):
		self.assertEqual(clean_names({"Income": "הכנסות"}), {"Income": "הכנסות"})

	def test_trims_surrounding_whitespace(self):
		self.assertEqual(clean_names({"  Equity  ": "  حقوق الملكية  "}), {"Equity": "حقوق الملكية"})

	def test_rejects_malformed_json(self):
		with self.assertRaises(ValueError):
			clean_names("{not json")

	def test_rejects_a_json_array(self):
		with self.assertRaises(ValueError):
			clean_names("[]")

	def test_rejects_an_empty_mapping(self):
		with self.assertRaises(ValueError):
			clean_names({})

	def test_rejects_a_blank_name(self):
		with self.assertRaises(ValueError):
			clean_names({"Expenses": "   "})

	def test_rejects_a_non_string_value(self):
		with self.assertRaises(ValueError):
			clean_names({"Expenses": 5})

	def test_rejects_an_overlong_name(self):
		with self.assertRaises(ValueError):
			clean_names({"Expenses": "x" * (MAX_ACCOUNT_NAME + 1)})

	def test_allows_a_name_at_the_limit(self):
		name = "x" * MAX_ACCOUNT_NAME
		self.assertEqual(clean_names({"Expenses": name}), {"Expenses": name})

	def test_rejects_line_breaks_and_tabs(self):
		for bad in ("two\nlines", "two\rlines", "two\tcols"):
			with self.assertRaises(ValueError):
				clean_names({"Expenses": bad})

	def test_rejects_two_roots_collapsing_onto_one_name(self):
		# Would make the second db write create a duplicate account name.
		with self.assertRaises(ValueError):
			clean_names({"Income": "الإيرادات", "Expenses": "الإيرادات"})


if __name__ == "__main__":
	unittest.main()
