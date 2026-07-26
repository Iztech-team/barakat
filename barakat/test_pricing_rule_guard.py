"""On-bench tests for the Pricing Rule open-shift guards.

Run on a site:
    bench --site <site> run-tests --module barakat.test_pricing_rule_guard
Not runnable on the Windows dev box (imports `frappe`).

These stub the open-shift lookup rather than creating real POS Opening Entries,
so they assert the RULE, not one site's data.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.validations import (
    guard_pricing_rule_delete,
    validate_pricing_rule_disable,
)

COMPANY = "Test Co"
OPEN_SHIFT = [{"name": "POS-OPE-0001", "pos_profile": "Main", "company": COMPANY}]


def _doc(disable=0, company=COMPANY, name="PRLE-0001", is_new=False):
    d = frappe._dict(name=name, disable=disable, company=company)
    # frappe._dict supports attribute assignment (it's dict.__setattr__), and
    # `d.is_new` becomes `d["is_new"]` — calling it as `doc.is_new()` then calls
    # this lambda, mirroring the real Document.is_new() method the guard relies on.
    d.is_new = lambda: is_new
    return d


class TestPricingRuleDeleteGuard(FrappeTestCase):
    def test_blocks_delete_while_a_shift_is_open(self):
        with patch("barakat.validations.open_shifts_for_company", return_value=OPEN_SHIFT):
            with self.assertRaises(frappe.ValidationError):
                guard_pricing_rule_delete(_doc(), "on_trash")

    def test_allows_delete_when_no_shift_is_open(self):
        with patch("barakat.validations.open_shifts_for_company", return_value=[]):
            guard_pricing_rule_delete(_doc(), "on_trash")

    def test_a_site_wide_rule_is_blocked_by_any_open_shift(self):
        # No company set means the rule applies everywhere, so any open shift counts.
        with patch("barakat.validations.open_shifts_for_company", return_value=OPEN_SHIFT) as m:
            with self.assertRaises(frappe.ValidationError):
                guard_pricing_rule_delete(_doc(company=None), "on_trash")
            m.assert_called_once_with(None)


class TestPricingRuleDisableGuard(FrappeTestCase):
    def test_blocks_disabling_while_a_shift_is_open(self):
        with patch("barakat.validations.open_shifts_for_company", return_value=OPEN_SHIFT):
            with patch.object(frappe.db, "get_value", return_value=0):
                with self.assertRaises(frappe.ValidationError):
                    validate_pricing_rule_disable(_doc(disable=1), "validate")

    def test_ignores_a_rule_that_was_already_disabled(self):
        # Editing an already-disabled rule must not be blocked.
        with patch("barakat.validations.open_shifts_for_company", return_value=OPEN_SHIFT):
            with patch.object(frappe.db, "get_value", return_value=1):
                validate_pricing_rule_disable(_doc(disable=1), "validate")

    def test_ignores_an_enabled_rule(self):
        with patch("barakat.validations.open_shifts_for_company", return_value=OPEN_SHIFT):
            validate_pricing_rule_disable(_doc(disable=0), "validate")

    def test_allows_creating_an_already_disabled_rule(self):
        # `validate` runs on insert too, and Frappe assigns doc.name BEFORE
        # run_before_save_methods() runs it — so frappe.db.get_value(doc.name)
        # would return None here too, indistinguishable from "was enabled
        # before" without the is_new() check this test pins. Creating an
        # already-disabled rule during an open shift is harmless: no till has
        # ever seen it, so none can grant it. get_value must not even be
        # consulted — the old code's None-means-"was enabled" reading is
        # exactly the bug this guards against.
        with patch("barakat.validations.open_shifts_for_company", return_value=OPEN_SHIFT):
            with patch.object(frappe.db, "get_value") as get_value:
                validate_pricing_rule_disable(_doc(disable=1, is_new=True), "validate")
                get_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
