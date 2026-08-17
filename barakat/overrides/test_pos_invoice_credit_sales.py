"""On-bench tests for the POS Invoice credit-sale guard.

Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_pos_invoice_credit_sales
Not runnable on the Windows dev box (imports `frappe`).

Same approach as test_pos_invoice_cashier_limits: the profile lookup and the
debt lookup are stubbed, so these assert the RULE rather than one site's data.
The debt stub is the interesting one — it returns the two terms separately
(consolidated, unconsolidated) precisely so the tests can prove the second is
counted. See docs/superpowers/specs/2026-08-17-pos-credit-sales-design.md.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.pos_invoice import BarakatPOSInvoice

PROFILE = "Main Branch pos profile - Test Co"
WALK_IN = "Default Customer"
COMPANY = "Test Co"


def _invoice(
    grand_total=100.0,
    paid_amount=100.0,
    customer="Ahmad",
    rounded_total=0.0,
    is_return=0,
    pos_profile=PROFILE,
):
    """A stand-in carrying only the fields the guard reads."""
    doc = MagicMock()
    doc.pos_profile = pos_profile
    doc.company = COMPANY
    doc.customer = customer
    doc.grand_total = grand_total
    doc.rounded_total = rounded_total
    doc.paid_amount = paid_amount
    doc.is_return = is_return
    doc.name = "ACC-PSINV-2026-00001"
    doc.precision = lambda _field: 2
    return doc


def _limits(allow_credit=1, walk_in=WALK_IN):
    return {
        "custom_allow_credit_sale": allow_credit,
        "customer": walk_in,
        "custom_allow_ad_hoc_item": 1,
        "custom_max_discount_percent": 100.0,
    }


def _guard(doc, limits=None, limit=500.0, consolidated=0.0, unconsolidated=0.0):
    """Run the guard with the two database reads stubbed."""
    with patch(
        "barakat.overrides.pos_invoice._profile_limits",
        return_value=_limits() if limits is None else limits,
    ), patch(
        "barakat.overrides.pos_invoice.customer_credit_limit", return_value=limit
    ), patch(
        "barakat.overrides.pos_invoice.customer_debt",
        return_value=(consolidated, unconsolidated),
    ):
        BarakatPOSInvoice.validate_credit_sale(doc)


class FullyPaidSalesAreUntouched(FrappeTestCase):
    def test_a_paid_sale_passes_even_with_no_limit(self):
        _guard(_invoice(100.0, 100.0), limit=None)

    def test_a_paid_sale_passes_even_when_credit_is_disabled(self):
        _guard(_invoice(100.0, 100.0), limits=_limits(allow_credit=0))

    def test_a_paid_sale_passes_for_the_walk_in_customer(self):
        _guard(_invoice(100.0, 100.0, customer=WALK_IN))

    def test_an_overpaid_sale_is_not_a_credit_sale(self):
        # Change was given. Negative debt must never read as borrowing.
        _guard(_invoice(100.0, 150.0), limit=None)

    def test_a_rounded_total_shortfall_is_not_a_credit_sale(self):
        # rounded_total wins over grand_total, and paid matches it exactly.
        _guard(_invoice(100.4, 100.0, rounded_total=100.0), limit=None)


class NotATill(FrappeTestCase):
    def test_an_invoice_with_no_profile_is_not_judged(self):
        # A consolidated or hand-made invoice. Its outstanding is the books'
        # business, not a cashier limit.
        _guard(_invoice(100.0, 0.0, pos_profile=None), limit=None)


class ReturnsAreExempt(FrappeTestCase):
    def test_a_credit_note_is_never_over_the_limit(self):
        # It REDUCES debt. Blocking it would trap an over-limit customer.
        _guard(
            _invoice(-100.0, 0.0, is_return=1),
            limit=None,
            consolidated=9999.0,
        )


class ProfileGate(FrappeTestCase):
    def test_credit_refused_when_the_till_may_not_sell_on_credit(self):
        with self.assertRaises(frappe.ValidationError):
            _guard(_invoice(100.0, 40.0), limits=_limits(allow_credit=0))

    def test_credit_allowed_when_the_till_may(self):
        _guard(_invoice(100.0, 40.0), limits=_limits(allow_credit=1))


class CustomerGate(FrappeTestCase):
    def test_the_walk_in_customer_may_not_borrow(self):
        with self.assertRaises(frappe.ValidationError):
            _guard(_invoice(100.0, 40.0, customer=WALK_IN))

    def test_a_named_customer_may(self):
        _guard(_invoice(100.0, 40.0, customer="Ahmad"))

    def test_a_profile_with_no_walk_in_configured_does_not_block_everyone(self):
        # `customer` empty must not turn into "block the empty-string customer".
        _guard(_invoice(100.0, 40.0, customer="Ahmad"), limits=_limits(walk_in=""))


class LimitRequired(FrappeTestCase):
    def test_no_limit_row_refuses_credit(self):
        with self.assertRaises(frappe.ValidationError):
            _guard(_invoice(100.0, 40.0), limit=None)

    def test_a_zero_limit_refuses_credit(self):
        # ERPNext would read 0 as "unlimited". We read it as "none".
        with self.assertRaises(frappe.ValidationError):
            _guard(_invoice(100.0, 40.0), limit=0)


class TheLimitItself(FrappeTestCase):
    def test_within_the_limit_is_allowed(self):
        _guard(_invoice(100.0, 40.0), limit=500.0)

    def test_exactly_at_the_limit_is_allowed(self):
        # ₪500 limit, nothing owed, borrowing exactly ₪500.
        _guard(_invoice(500.0, 0.0), limit=500.0)

    def test_one_agora_over_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            _guard(_invoice(500.01, 0.0), limit=500.0)

    def test_existing_consolidated_debt_counts(self):
        with self.assertRaises(frappe.ValidationError):
            _guard(_invoice(300.0, 0.0), limit=500.0, consolidated=300.0)

    def test_unconsolidated_debt_counts(self):
        # THE ONE THAT MATTERS. ₪300 owed on POS invoices that have not been
        # merged yet, so ERPNext's own GL-based check cannot see a penny of it.
        # If this test fails, the shift close will throw instead.
        with self.assertRaises(frappe.ValidationError):
            _guard(_invoice(300.0, 0.0), limit=500.0, unconsolidated=300.0)

    def test_both_kinds_of_debt_add_up(self):
        with self.assertRaises(frappe.ValidationError):
            _guard(
                _invoice(150.0, 0.0),
                limit=500.0,
                consolidated=200.0,
                unconsolidated=200.0,
            )

    def test_filling_the_last_of_the_headroom_is_allowed(self):
        _guard(
            _invoice(200.0, 0.0),
            limit=500.0,
            consolidated=200.0,
            unconsolidated=100.0,
        )

    def test_a_part_paid_sale_only_borrows_the_difference(self):
        # ₪400 bill, ₪350 cash, so only ₪50 is credit — inside a ₪100 limit
        # that the full ₪400 would have blown.
        _guard(_invoice(400.0, 350.0), limit=100.0)


class TheMessage(FrappeTestCase):
    def test_it_names_the_headroom_and_the_shortfall(self):
        # A refusal the cashier can act on: how much is left, and what was asked.
        with self.assertRaises(frappe.ValidationError) as caught:
            _guard(_invoice(300.0, 0.0), limit=500.0, unconsolidated=400.0)
        message = str(caught.exception)
        self.assertIn("Ahmad", message)
