"""Pure, Frappe-free tests for the tax-template duplicate-account rule.

Runs locally:  python -m unittest barakat.test_tax_template_rows
"""

import unittest

from barakat.tax_template_rows import first_duplicate_account, normalize_account_head


class NormalizeAccountHead(unittest.TestCase):
    def test_trims_and_folds_case(self):
        self.assertEqual(normalize_account_head("  VAT - BAM  "), "vat - bam")

    def test_arabic_passes_through(self):
        self.assertEqual(normalize_account_head(" ض.ق.م - BAM "), "ض.ق.م - bam")

    def test_none_is_blank(self):
        self.assertEqual(normalize_account_head(None), "")


class FirstDuplicateAccount(unittest.TestCase):
    def test_distinct_accounts_are_clean(self):
        self.assertIsNone(
            first_duplicate_account(["VAT - BAM", "Municipal Levy - BAM", "ض.ق.م - BAM"])
        )

    def test_exact_repeat_is_reported(self):
        self.assertEqual(first_duplicate_account(["VAT - BAM", "VAT - BAM"]), "VAT - BAM")

    def test_case_and_space_variant_is_a_repeat(self):
        # Quoted as the user typed it, not as the folded key.
        self.assertEqual(
            first_duplicate_account(["VAT - BAM", "  vat - bam "]), "  vat - bam "
        )

    def test_repeat_is_found_across_a_gap(self):
        self.assertEqual(
            first_duplicate_account(["VAT - BAM", "Levy - BAM", "VAT - BAM"]),
            "VAT - BAM",
        )

    def test_the_second_occurrence_is_the_one_returned(self):
        # Three of a kind reports the SECOND row, so the message points at the
        # first row the user actually has to change.
        rows = ["VAT - BAM", "vat - BAM", "VAT - BAM"]
        self.assertEqual(first_duplicate_account(rows), "vat - BAM")

    def test_blank_rows_are_skipped(self):
        # `account_head` is reqd; Frappe reports the empty row itself.
        self.assertIsNone(first_duplicate_account(["", None, "   ", "VAT - BAM"]))

    def test_blank_rows_do_not_hide_a_real_duplicate(self):
        self.assertEqual(
            first_duplicate_account(["VAT - BAM", "", "VAT - BAM"]), "VAT - BAM"
        )

    def test_empty_table_is_clean(self):
        self.assertIsNone(first_duplicate_account([]))

    def test_single_row_is_clean(self):
        self.assertIsNone(first_duplicate_account(["VAT - BAM"]))


if __name__ == "__main__":
    unittest.main()
