"""Pure, Frappe-free tests for settling a customer's debt.

Runs locally:  python -m unittest barakat.test_credit_repayment

Two properties most of these defend:

  A repayment may never exceed the debt. The cap is recomputed server-side
  against a freshly-read figure, because the till's number is a snapshot and
  another till may have sold to the same customer since.

  Over-allocating a Payment Entry makes ERPNext throw at submit — which takes
  the cashier's money and records nothing. So the allocator is capped twice,
  per invoice and in total, and both caps are tested.
"""

import unittest

from barakat.credit_repayment import (
    allocate_repayment,
    repayment_over_debt,
    valid_repayment,
)


class RepaymentOverDebt(unittest.TestCase):
    def test_paying_less_than_owed_is_fine(self):
        self.assertFalse(repayment_over_debt(100.0, 250.0))

    def test_paying_exactly_the_debt_is_fine(self):
        # The commonest case of all: "how much do I owe?" — "₪250" — "here."
        # Rejecting this would make the feature useless.
        self.assertFalse(repayment_over_debt(250.0, 250.0))

    def test_one_agora_over_is_over(self):
        self.assertTrue(repayment_over_debt(250.01, 250.0))

    def test_paying_anything_when_nothing_is_owed_is_over(self):
        # Decided deliberately: we do not take money on account from a customer
        # with a clean sheet. A cashier who does that has almost certainly
        # picked the wrong customer.
        self.assertTrue(repayment_over_debt(50.0, 0.0))

    def test_a_debt_that_reads_as_a_credit_balance_accepts_nothing(self):
        # Negative owed means the customer is IN CREDIT with us.
        self.assertTrue(repayment_over_debt(10.0, -40.0))

    def test_float_representation_alone_is_forgiven(self):
        # 0.1 + 0.2 owed, paid in full. The tolerance exists for exactly this
        # and must not be wide enough to forgive a real agora.
        self.assertFalse(repayment_over_debt(0.30000000000000004, 0.3))

    def test_paying_a_debt_made_up_of_many_small_invoices(self):
        self.assertFalse(repayment_over_debt(0.3, 0.1 + 0.2))


class ValidRepayment(unittest.TestCase):
    def test_a_normal_repayment_is_allowed(self):
        self.assertEqual(valid_repayment(100.0, 250.0), (True, None))

    def test_zero_is_refused(self):
        # Would submit a Payment Entry for nothing, and print a receipt saying
        # a customer paid when they did not.
        self.assertEqual(valid_repayment(0.0, 250.0), (False, "amount_not_positive"))

    def test_a_negative_amount_is_refused(self):
        # A refund dressed as a repayment. Refunds have their own path, which
        # returns goods; this one would just move money with no record of why.
        self.assertEqual(valid_repayment(-50.0, 250.0), (False, "amount_not_positive"))

    def test_a_customer_who_owes_nothing_is_refused(self):
        self.assertEqual(valid_repayment(50.0, 0.0), (False, "nothing_owed"))

    def test_more_than_owed_is_refused(self):
        self.assertEqual(valid_repayment(300.0, 250.0), (False, "over_debt"))

    def test_the_reason_distinguishes_the_two_refusals(self):
        # The till shows a different sentence for each, so they must not
        # collapse: "this customer owes nothing" and "that is more than they
        # owe" send the cashier to different places.
        self.assertNotEqual(
            valid_repayment(50.0, 0.0)[1], valid_repayment(300.0, 250.0)[1]
        )


class AllocateRepayment(unittest.TestCase):
    def test_one_invoice_paid_in_full(self):
        allocations, left = allocate_repayment(100.0, [("SI-1", 100.0)])
        self.assertEqual(allocations, [("SI-1", 100.0)])
        self.assertEqual(left, 0.0)

    def test_oldest_first(self):
        # The order given is the order settled — the caller sorts, so the
        # ageing report and the allocation agree.
        allocations, left = allocate_repayment(
            120.0, [("SI-old", 100.0), ("SI-new", 100.0)]
        )
        self.assertEqual(allocations, [("SI-old", 100.0), ("SI-new", 20.0)])
        self.assertEqual(left, 0.0)

    def test_never_allocates_more_than_an_invoice_owes(self):
        # Over-allocating makes ERPNext throw at submit, which takes the
        # cashier's money and records nothing.
        allocations, left = allocate_repayment(500.0, [("SI-1", 100.0)])
        self.assertEqual(allocations, [("SI-1", 100.0)])
        self.assertEqual(left, 400.0)

    def test_never_allocates_more_than_the_payment(self):
        allocations, left = allocate_repayment(
            50.0, [("SI-1", 100.0), ("SI-2", 100.0)]
        )
        self.assertEqual(allocations, [("SI-1", 50.0)])
        self.assertEqual(left, 0.0)

    def test_the_remainder_is_what_covers_an_unconsolidated_shift(self):
        # NOT an error and NOT change. A credit sale from a still-open shift is
        # a POS Invoice, which a Payment Entry may not reference, so the money
        # covering it goes on account until the shift consolidates.
        allocations, left = allocate_repayment(300.0, [("SI-1", 100.0)])
        self.assertEqual(allocations, [("SI-1", 100.0)])
        self.assertEqual(left, 200.0)

    def test_a_customer_with_no_consolidated_invoices_allocates_nothing(self):
        # Everything they owe is from the open shift.
        allocations, left = allocate_repayment(200.0, [])
        self.assertEqual(allocations, [])
        self.assertEqual(left, 200.0)

    def test_an_invoice_settled_since_it_was_read_is_skipped(self):
        # Another till took a payment between the read and the write.
        allocations, left = allocate_repayment(
            100.0, [("SI-paid", 0.0), ("SI-1", 100.0)]
        )
        self.assertEqual(allocations, [("SI-1", 100.0)])
        self.assertEqual(left, 0.0)

    def test_a_credit_note_is_skipped_rather_than_subtracted(self):
        # A negative allocation would silently increase what the rest of the
        # payment has to cover, and ERPNext refuses negative rows anyway.
        allocations, left = allocate_repayment(
            100.0, [("CN-1", -40.0), ("SI-1", 100.0)]
        )
        self.assertEqual(allocations, [("SI-1", 100.0)])
        self.assertEqual(left, 0.0)

    def test_no_zero_rows_are_emitted(self):
        # A reference row allocating nothing is noise in the ledger.
        allocations, _ = allocate_repayment(
            100.0, [("SI-1", 100.0), ("SI-2", 100.0), ("SI-3", 100.0)]
        )
        self.assertEqual(allocations, [("SI-1", 100.0)])
        self.assertTrue(all(amount > 0 for _, amount in allocations))

    def test_allocations_sum_to_the_payment_when_it_is_fully_absorbed(self):
        allocations, left = allocate_repayment(
            250.0, [("SI-1", 100.0), ("SI-2", 100.0), ("SI-3", 100.0)]
        )
        self.assertEqual(round(sum(a for _, a in allocations), 2), 250.0)
        self.assertEqual(left, 0.0)

    def test_allocations_plus_the_remainder_always_equal_the_payment(self):
        # The invariant the whole function exists to keep. Money that is neither
        # allocated nor reported as a remainder has simply vanished.
        for amount, invoices in [
            (100.0, [("A", 100.0)]),
            (250.0, [("A", 100.0), ("B", 100.0)]),
            (75.5, [("A", 20.25), ("B", 30.25)]),
            (0.3, [("A", 0.1), ("B", 0.1)]),
            (999.99, []),
        ]:
            allocations, left = allocate_repayment(amount, invoices)
            total = round(sum(a for _, a in allocations) + left, 2)
            self.assertEqual(total, round(amount, 2), f"{amount} across {invoices}")

    def test_amounts_that_binary_floating_point_cannot_hold(self):
        allocations, left = allocate_repayment(0.3, [("A", 0.1), ("B", 0.2)])
        self.assertEqual(allocations, [("A", 0.1), ("B", 0.2)])
        self.assertEqual(left, 0.0)

    def test_paying_nothing_allocates_nothing(self):
        # Refused earlier by `valid_repayment`; proven here not to misbehave if
        # it ever arrives, because a zero that produced a row would submit an
        # empty Payment Entry.
        self.assertEqual(allocate_repayment(0.0, [("A", 100.0)]), ([], 0.0))


if __name__ == "__main__":
    unittest.main()
