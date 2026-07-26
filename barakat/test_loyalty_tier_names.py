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

    def test_duplicate_at_the_length_cap_is_truncated_to_fit(self):
        # tier_name is a Frappe `Data` field -> varchar(140). Appending " (2)"
        # to an already-140-character name must truncate the base, never grow
        # past the column width.
        name = "a" * 140
        renames = resolve_exact_duplicates([name, name])
        renamed = renames[1]
        self.assertLessEqual(len(renamed), 140)
        self.assertNotEqual(renamed, name)
        self.assertTrue(renamed.endswith(" (2)"))

    def test_duplicate_well_under_the_cap_is_not_truncated(self):
        # Guards against an over-eager truncation that clips short names too.
        self.assertEqual(
            resolve_exact_duplicates(["Gold", "Gold"]), {1: "Gold (2)"}
        )

    def test_double_digit_counter_still_respects_the_cap(self):
        # 12 copies of one over-length name -> 11 duplicates, so the counter
        # walks 2, 3, ..., 12 and crosses into double digits partway through.
        name = "b" * 140
        names = [name] * 12
        renames = resolve_exact_duplicates(names)

        self.assertEqual(len(renames), 11)
        for renamed in renames.values():
            self.assertLessEqual(len(renamed), 140)

        # Single-digit counters (2-9): " (N)" is 4 chars, so 136 base chars.
        self.assertEqual(renames[1], "b" * 136 + " (2)")
        self.assertEqual(renames[8], "b" * 136 + " (9)")
        # Double-digit counters (10-12): " (NN)" is 5 chars, so 135 base chars.
        self.assertEqual(renames[9], "b" * 135 + " (10)")
        self.assertEqual(renames[10], "b" * 135 + " (11)")
        self.assertEqual(renames[11], "b" * 135 + " (12)")
        self.assertEqual(len(renames[11]), 140)
