"""On-bench tests for the till's customer-creation guard.

Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_customer_pos_guard
Not runnable on the Windows dev box (imports `frappe`).
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.customer_pos_guard import guard_pos_customer_creation

PROFILE = "Main Branch pos profile - Test Co"


def _customer(stamp):
    doc = MagicMock()
    doc.get = lambda field, default=None: (
        stamp if field == "custom_pos_profile" else default
    )
    doc.customer_name = "Walk In"
    return doc


class AdminPanelPathIsUntouched(FrappeTestCase):
    def test_no_stamp_is_allowed(self):
        # This is the AP creating a customer. It must never be blocked, and it
        # must not even cost a database read.
        with patch(
            "barakat.overrides.customer_pos_guard.frappe.db.get_value"
        ) as get_value:
            guard_pos_customer_creation(_customer(None))
            get_value.assert_not_called()

    def test_blank_stamp_is_allowed(self):
        with patch(
            "barakat.overrides.customer_pos_guard.frappe.db.get_value"
        ) as get_value:
            guard_pos_customer_creation(_customer("   "))
            get_value.assert_not_called()


class TillPath(FrappeTestCase):
    def test_allowed_when_flag_on(self):
        with patch(
            "barakat.overrides.customer_pos_guard.frappe.db.get_value",
            return_value=1,
        ):
            guard_pos_customer_creation(_customer(PROFILE))  # must not raise

    def test_rejected_when_flag_off(self):
        with patch(
            "barakat.overrides.customer_pos_guard.frappe.db.get_value",
            return_value=0,
        ):
            with self.assertRaises(frappe.ValidationError):
                guard_pos_customer_creation(_customer(PROFILE))

    def test_rejected_when_profile_does_not_exist(self):
        # Fails closed. A till that cannot name its own profile has no business
        # creating customers, and returning None here would otherwise read as
        # "flag off" by accident rather than by decision.
        with patch(
            "barakat.overrides.customer_pos_guard.frappe.db.get_value",
            return_value=None,
        ):
            with self.assertRaises(frappe.ValidationError):
                guard_pos_customer_creation(_customer(PROFILE))
