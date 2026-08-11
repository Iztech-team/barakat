"""On-bench tests for the POS Invoice cashier-limit guards.

Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_pos_invoice_cashier_limits
Not runnable on the Windows dev box (imports `frappe`).

These stub the POS Profile lookup rather than creating real profiles, so they
assert the RULE, not one site's data — same approach as
test_pos_profile_warehouse_guard.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.pos_invoice import BarakatPOSInvoice

PROFILE = "Main Branch pos profile - Test Co"


def _invoice(items, discount_amount=0.0, total=0.0, grand_total=100.0, is_return=0):
    """A stand-in carrying only the fields the guard reads."""
    doc = MagicMock()
    doc.pos_profile = PROFILE
    doc.items = [MagicMock(item_code=code) for code in items]
    doc.discount_amount = discount_amount
    doc.total = total
    doc.grand_total = grand_total
    doc.is_return = is_return
    doc.precision = lambda _field: 2
    return doc


def _limits(ad_hoc=0, max_discount=100.0):
    return {
        "custom_allow_ad_hoc_item": ad_hoc,
        "custom_max_discount_percent": max_discount,
    }


class AdHocItemGuard(FrappeTestCase):
    def test_misc_line_rejected_when_flag_off(self):
        doc = _invoice(["APPLE", "MISC"])
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value=_limits(ad_hoc=0),
        ):
            with self.assertRaises(frappe.ValidationError):
                BarakatPOSInvoice.validate_cashier_limits(doc)

    def test_misc_line_allowed_when_flag_on(self):
        doc = _invoice(["APPLE", "MISC"])
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value=_limits(ad_hoc=1),
        ):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise

    def test_normal_cart_unaffected_when_flag_off(self):
        doc = _invoice(["APPLE", "BREAD"])
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value=_limits(ad_hoc=0),
        ):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise


class DiscountCapGuard(FrappeTestCase):
    def test_over_cap_rejected(self):
        doc = _invoice(["APPLE"], discount_amount=20.0, total=100.0)
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value=_limits(max_discount=10.0),
        ):
            with self.assertRaises(frappe.ValidationError):
                BarakatPOSInvoice.validate_cashier_limits(doc)

    def test_at_cap_allowed(self):
        doc = _invoice(["APPLE"], discount_amount=10.0, total=100.0)
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value=_limits(max_discount=10.0),
        ):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise

    def test_refund_is_exempt(self):
        doc = _invoice(["APPLE"], discount_amount=99.0, total=100.0, is_return=1)
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value=_limits(max_discount=10.0),
        ):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise

    def test_free_order_is_exempt(self):
        # push-orders.ts discounts the whole subtotal when a real-value order
        # rounds to a zero grand total, so it consolidates cleanly at shift
        # close. Without this exemption every such order is rejected.
        doc = _invoice(
            ["APPLE"], discount_amount=0.5, total=0.5, grand_total=0.0
        )
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value=_limits(max_discount=10.0),
        ):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise

    def test_an_unbackfilled_profile_does_not_block_discounts(self):
        # A profile the patch has not reached returns None for the cap. Reading
        # that as 0 would reject every discounted sale at that shop.
        doc = _invoice(["APPLE"], discount_amount=50.0, total=100.0)
        with patch(
            "barakat.overrides.pos_invoice._profile_limits",
            return_value={"custom_allow_ad_hoc_item": 1},
        ):
            BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise


class NoProfile(FrappeTestCase):
    def test_invoice_without_a_profile_is_skipped(self):
        # Consolidated / manually created invoices may carry no pos_profile.
        # They are not a till and must never be blocked by a till's limits.
        doc = _invoice(["MISC"], discount_amount=99.0, total=100.0)
        doc.pos_profile = None
        BarakatPOSInvoice.validate_cashier_limits(doc)  # must not raise
