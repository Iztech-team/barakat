"""On-bench tests for the POS Profile warehouse open-shift guard.

Run on a site:
    bench --site <site> run-tests --module barakat.test_pos_profile_warehouse_guard
Not runnable on the Windows dev box (imports `frappe`).

These stub the open-shift lookup rather than creating real POS Opening Entries,
so they assert the RULE, not one site's data — same approach as
test_pricing_rule_guard.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.validations import (
    POSProfileWarehouseLocked,
    validate_pos_profile_warehouse_change,
)

COMPANY = "Test Co"
PROFILE = "Main Branch pos profile - Test Co"
OPEN_SHIFT = [{"name": "POS-OPE-0001", "pos_profile": PROFILE, "company": COMPANY}]


def _doc(warehouse="New - TC", name=PROFILE, is_new=False):
    d = frappe._dict(name=name, warehouse=warehouse, company=COMPANY)
    # Mirrors Document.is_new(); see the note in test_pricing_rule_guard.
    d.is_new = lambda: is_new
    return d


class TestPOSProfileWarehouseGuard(FrappeTestCase):
    def test_blocks_a_warehouse_change_while_a_shift_is_open(self):
        with patch(
            "barakat.validations.open_shifts_for_pos_profile", return_value=OPEN_SHIFT
        ):
            with patch.object(frappe.db, "get_value", return_value="Old - TC"):
                with self.assertRaises(frappe.ValidationError):
                    validate_pos_profile_warehouse_change(_doc(), "validate")

    def test_allows_the_change_when_no_shift_is_open(self):
        with patch("barakat.validations.open_shifts_for_pos_profile", return_value=[]):
            with patch.object(frappe.db, "get_value", return_value="Old - TC"):
                validate_pos_profile_warehouse_change(_doc(), "validate")

    def test_ignores_a_save_that_does_not_touch_the_warehouse(self):
        # Editing anything else on the profile mid-shift stays allowed — only the
        # warehouse invalidates the till's stock figures.
        with patch(
            "barakat.validations.open_shifts_for_pos_profile", return_value=OPEN_SHIFT
        ) as m:
            with patch.object(frappe.db, "get_value", return_value="Same - TC"):
                validate_pos_profile_warehouse_change(
                    _doc(warehouse="Same - TC"), "validate"
                )
            m.assert_not_called()

    def test_ignores_a_brand_new_profile(self):
        # `validate` runs on insert and Frappe has already assigned the name, so the
        # lookup would return None and read as a change. A new profile cannot have
        # an open shift anyway.
        with patch(
            "barakat.validations.open_shifts_for_pos_profile", return_value=OPEN_SHIFT
        ) as m:
            validate_pos_profile_warehouse_change(_doc(is_new=True), "validate")
            m.assert_not_called()

    def test_scopes_the_lookup_to_THIS_profile(self):
        # Another profile's open shift in the same company must not block the edit —
        # its tills read a different warehouse entirely.
        with patch(
            "barakat.validations.open_shifts_for_pos_profile", return_value=[]
        ) as m:
            with patch.object(frappe.db, "get_value", return_value="Old - TC"):
                validate_pos_profile_warehouse_change(_doc(), "validate")
            m.assert_called_once_with(PROFILE)

    def test_setting_a_warehouse_for_the_first_time_is_still_guarded(self):
        # None -> something is a change too: the till was selling with no stock
        # truth at all and would suddenly gain one mid-shift.
        with patch(
            "barakat.validations.open_shifts_for_pos_profile", return_value=OPEN_SHIFT
        ):
            with patch.object(frappe.db, "get_value", return_value=None):
                with self.assertRaises(frappe.ValidationError):
                    validate_pos_profile_warehouse_change(_doc(), "validate")

    def test_raises_the_dedicated_exception_type(self):
        # The proxy matches on exc_type, not on the message: Frappe translates
        # thrown messages per user language, so a text match would work in
        # English and silently stop working for an Arabic or Hebrew operator.
        with patch(
            "barakat.validations.open_shifts_for_pos_profile", return_value=OPEN_SHIFT
        ):
            with patch.object(frappe.db, "get_value", return_value="Old - TC"):
                with self.assertRaises(POSProfileWarehouseLocked):
                    validate_pos_profile_warehouse_change(_doc(), "validate")


if __name__ == "__main__":
    unittest.main()
