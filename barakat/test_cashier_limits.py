"""Pure, Frappe-free tests for the cashier-limit decisions.

Runs locally:  python -m unittest barakat.test_cashier_limits

Every rule the POS Profile's cashier limits enforce is decided in
barakat.cashier_limits, so the Frappe hooks stay thin and the arithmetic is
provable without a bench.
"""

import unittest

from barakat.cashier_limits import (
    AD_HOC_ITEM_CODE,
    MAX_DISCOUNT_UNLIMITED,
    discount_over_cap,
    has_ad_hoc_line,
)


class DiscountCap(unittest.TestCase):
    def test_under_the_cap_is_allowed(self):
        # 5 off 100 is 5%, cap is 10%.
        self.assertFalse(discount_over_cap(5.0, 100.0, 10.0))

    def test_over_the_cap_is_rejected(self):
        # 20 off 100 is 20%, cap is 10%.
        self.assertTrue(discount_over_cap(20.0, 100.0, 10.0))

    def test_exactly_at_the_cap_is_allowed(self):
        self.assertFalse(discount_over_cap(10.0, 100.0, 10.0))

    def test_exactly_at_the_cap_survives_rounding_down(self):
        # 10% of 33.33 is 3.333, which the till rounds DOWN to 3.33 before
        # sending. A bare `>` against the unrounded 3.333 rejects this.
        self.assertFalse(discount_over_cap(3.33, 33.33, 10.0))

    def test_exactly_at_the_cap_survives_rounding_up(self):
        # 10% of 33.35 is 3.335, which the till rounds UP to 3.34. The server
        # must accept the till's own maximum: comparing 3.34 against an
        # unrounded 3.335 would reject the largest discount the keypad allows.
        self.assertFalse(discount_over_cap(3.34, 33.35, 10.0))

    def test_above_the_rounded_cap_is_rejected(self):
        # The till would have sent 3.33 here (10% of 33.33 rounds down), so
        # 3.34 is a real overcharge and not a rounding artefact.
        self.assertTrue(discount_over_cap(3.34, 33.33, 10.0))

    def test_ten_agorot_over_the_cap_is_rejected(self):
        self.assertTrue(discount_over_cap(3.44, 33.33, 10.0))

    def test_never_stricter_than_the_till(self):
        # 50% of 19.99 is 9.995. The till's roundMoney multiplies by 100 first
        # and lands on 9.99; this module lands on 10.00. The disagreement is
        # tolerable ONLY in this direction — the server allowing a hair more
        # than the keypad ever offers. The reverse would reject a discount the
        # cashier was invited to enter.
        self.assertFalse(discount_over_cap(9.99, 19.99, 50.0))
        self.assertFalse(discount_over_cap(10.00, 19.99, 50.0))
        self.assertTrue(discount_over_cap(10.01, 19.99, 50.0))

    def test_unlimited_cap_allows_a_full_discount(self):
        self.assertFalse(discount_over_cap(100.0, 100.0, MAX_DISCOUNT_UNLIMITED))

    def test_zero_cap_rejects_any_discount(self):
        self.assertTrue(discount_over_cap(0.01, 100.0, 0.0))

    def test_zero_cap_allows_no_discount(self):
        self.assertFalse(discount_over_cap(0.0, 100.0, 0.0))

    def test_zero_total_is_skipped(self):
        # Nothing to take a percentage of, and dividing would raise.
        self.assertFalse(discount_over_cap(0.0, 0.0, 10.0))
        self.assertFalse(discount_over_cap(5.0, 0.0, 10.0))

    def test_negative_total_is_skipped(self):
        self.assertFalse(discount_over_cap(5.0, -100.0, 10.0))

    def test_none_cap_is_treated_as_unlimited(self):
        # An un-backfilled profile reads None. It must NOT read as zero.
        self.assertFalse(discount_over_cap(50.0, 100.0, None))


class AdHocLines(unittest.TestCase):
    def test_detects_a_misc_line(self):
        self.assertTrue(has_ad_hoc_line(["APPLE", AD_HOC_ITEM_CODE]))

    def test_a_normal_cart_has_none(self):
        self.assertFalse(has_ad_hoc_line(["APPLE", "BREAD"]))

    def test_empty_cart_has_none(self):
        self.assertFalse(has_ad_hoc_line([]))

    def test_ignores_case_and_padding(self):
        # ERPNext item codes are stored as sent; be liberal about what arrives
        # rather than let a stray space defeat the guard.
        self.assertTrue(has_ad_hoc_line([" misc "]))

    def test_tolerates_none_entries(self):
        self.assertFalse(has_ad_hoc_line([None, "APPLE"]))


if __name__ == "__main__":
    unittest.main()
