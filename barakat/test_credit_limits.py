"""Pure, Frappe-free tests for the credit-sale decisions.

Runs locally:  python -m unittest barakat.test_credit_limits

The arithmetic that decides whether a customer may take a sale on credit lives
in barakat.credit_limits, so it is provable without a bench. The one property
worth stating up front, because most of these tests exist to defend it:
UNCONSOLIDATED debt counts. ERPNext's own credit check reads a GL-based
outstanding, and a POS Invoice posts no GL until shift close — so a rule that
looked only at the GL would pass every sale in a shift and then jam the close.
"""

import unittest

from barakat.credit_limits import (
    NO_CREDIT,
    credit_headroom,
    credit_limit_of,
    credit_over_limit,
    may_take_credit,
    total_owed,
)


class CreditLimitOf(unittest.TestCase):
    def test_a_plain_number_is_the_limit(self):
        self.assertEqual(credit_limit_of(500.0), 500.0)

    def test_a_string_from_the_database_is_accepted(self):
        self.assertEqual(credit_limit_of("500"), 500.0)

    def test_none_means_no_credit(self):
        self.assertEqual(credit_limit_of(None), NO_CREDIT)

    def test_zero_means_no_credit_not_unlimited(self):
        # The deliberate divergence from ERPNext, where 0 disables the check.
        self.assertEqual(credit_limit_of(0), NO_CREDIT)

    def test_a_negative_limit_reads_as_none(self):
        self.assertEqual(credit_limit_of(-100), NO_CREDIT)

    def test_rubbish_reads_as_none_rather_than_raising(self):
        self.assertEqual(credit_limit_of("abc"), NO_CREDIT)
        self.assertEqual(credit_limit_of(""), NO_CREDIT)


class MayTakeCredit(unittest.TestCase):
    def test_a_customer_with_a_limit_may(self):
        self.assertTrue(may_take_credit(500))

    def test_a_customer_with_no_row_may_not(self):
        self.assertFalse(may_take_credit(None))

    def test_a_customer_with_a_zero_limit_may_not(self):
        self.assertFalse(may_take_credit(0))


class TotalOwed(unittest.TestCase):
    def test_both_terms_are_counted(self):
        self.assertEqual(total_owed(300.0, 200.0), 500.0)

    def test_unconsolidated_alone_is_still_debt(self):
        # The whole point: nothing has reached the GL yet, but ₪200 is owed.
        self.assertEqual(total_owed(0.0, 200.0), 200.0)

    def test_missing_terms_read_as_zero(self):
        self.assertEqual(total_owed(None, None), 0.0)

    def test_agorot_do_not_drift(self):
        self.assertEqual(total_owed(0.1, 0.2), 0.3)


class CreditHeadroom(unittest.TestCase):
    def test_an_untouched_limit_is_all_headroom(self):
        self.assertEqual(credit_headroom(500, 0, 0), 500.0)

    def test_consolidated_debt_eats_headroom(self):
        self.assertEqual(credit_headroom(500, 300, 0), 200.0)

    def test_unconsolidated_debt_eats_headroom_too(self):
        # If this returned 500 the shift-close bomb is live.
        self.assertEqual(credit_headroom(500, 0, 300), 200.0)

    def test_both_kinds_are_subtracted(self):
        self.assertEqual(credit_headroom(500, 300, 150), 50.0)

    def test_a_customer_at_their_limit_has_none(self):
        self.assertEqual(credit_headroom(500, 500, 0), 0.0)

    def test_a_customer_past_their_limit_reports_zero_not_negative(self):
        self.assertEqual(credit_headroom(500, 800, 0), 0.0)

    def test_no_limit_means_no_headroom(self):
        self.assertEqual(credit_headroom(None, 0, 0), 0.0)
        self.assertEqual(credit_headroom(0, 0, 0), 0.0)


class CreditOverLimit(unittest.TestCase):
    def test_within_the_limit_is_allowed(self):
        self.assertFalse(credit_over_limit(100, 500, 0, 0))

    def test_exactly_at_the_limit_is_allowed(self):
        # The boundary belongs to the customer.
        self.assertFalse(credit_over_limit(500, 500, 0, 0))

    def test_one_agora_over_is_refused(self):
        self.assertTrue(credit_over_limit(500.01, 500, 0, 0))

    def test_existing_consolidated_debt_counts(self):
        self.assertTrue(credit_over_limit(300, 500, 300, 0))

    def test_existing_unconsolidated_debt_counts(self):
        # The bomb, as a unit test: ₪300 already owed on invoices that have not
        # been merged yet, ₪300 more asked for, ₪500 limit.
        self.assertTrue(credit_over_limit(300, 500, 0, 300))

    def test_the_two_kinds_of_debt_add_up(self):
        # ₪200 + ₪200 owed, ₪150 asked for, ₪500 limit → ₪550.
        self.assertTrue(credit_over_limit(150, 500, 200, 200))

    def test_filling_the_remaining_headroom_exactly_is_allowed(self):
        self.assertFalse(credit_over_limit(200, 500, 200, 100))

    def test_a_fully_paid_sale_is_never_over_the_limit(self):
        # No debt taken on, so the limit is irrelevant even at zero headroom.
        self.assertFalse(credit_over_limit(0, 500, 500, 0))

    def test_a_negative_debt_is_not_a_credit_sale(self):
        # A return. It reduces debt; it must never be judged as taking it on.
        self.assertFalse(credit_over_limit(-50, 500, 500, 0))

    def test_no_configured_limit_refuses_any_debt(self):
        self.assertTrue(credit_over_limit(1, None, 0, 0))
        self.assertTrue(credit_over_limit(1, 0, 0, 0))

    def test_no_configured_limit_still_allows_a_paid_sale(self):
        self.assertFalse(credit_over_limit(0, None, 0, 0))

    def test_a_customer_already_past_their_limit_may_take_nothing(self):
        self.assertTrue(credit_over_limit(0.01, 500, 600, 0))

    def test_float_noise_does_not_refuse_a_legitimate_sale(self):
        # 0.1 + 0.2 arithmetic against a limit of exactly 0.3.
        self.assertFalse(credit_over_limit(0.2, 0.3, 0.1, 0))

    def test_precision_is_respected_for_a_zero_decimal_currency(self):
        # A currency with no minor unit: 500 is the ceiling and 501 is over.
        self.assertFalse(credit_over_limit(500, 500, 0, 0, precision=0))
        self.assertTrue(credit_over_limit(501, 500, 0, 0, precision=0))


if __name__ == "__main__":
    unittest.main()
