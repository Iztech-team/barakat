"""On-bench tests for the POS Profile account validation.

Run on a site:
    bench --site <site> run-tests --module barakat.test_validations
Not runnable on the Windows dev box (imports `frappe`).

These stub the Account/Company lookups rather than depending on a real chart of
accounts, so they assert the RULE, not one site's data.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.validations import SALARY_ADVANCE_FIELD, validate_pos_profile_accounts

COMPANY = "Test Co"
DEBTORS = "Debtors - TC"


def _lookup(account_type="Receivable", is_group=0, default_receivable=DEBTORS):
    """Stand in for frappe.db.get_value, dispatching on doctype."""

    def side_effect(doctype, name, fields=None, as_dict=False, **kwargs):
        if doctype == "Account":
            return frappe._dict(
                {
                    "account_type": account_type,
                    "root_type": "Asset",
                    "company": COMPANY,
                    "is_group": is_group,
                }
            )
        if doctype == "Company":
            return default_receivable
        return None

    return side_effect


def _doc(account):
    return frappe._dict({"company": COMPANY, SALARY_ADVANCE_FIELD: account})


class SalaryAdvanceAccount(FrappeTestCase):
    def test_rejects_company_default_receivable(self):
        """Debtors passes the generic Receivable rule — this guard is what stops it."""
        with patch("frappe.db.get_value", side_effect=_lookup()):
            with self.assertRaises(frappe.ValidationError):
                validate_pos_profile_accounts(_doc(DEBTORS), None)

    def test_allows_a_dedicated_advances_account(self):
        with patch("frappe.db.get_value", side_effect=_lookup()):
            validate_pos_profile_accounts(
                _doc("Employee Advances - TC"), None
            )  # must not raise

    def test_still_rejects_a_non_receivable_account(self):
        """The original rule must survive the new guard."""
        with patch("frappe.db.get_value", side_effect=_lookup(account_type="Bank")):
            with self.assertRaises(frappe.ValidationError):
                validate_pos_profile_accounts(_doc("Some Bank - TC"), None)

    def test_no_company_on_the_profile_does_not_crash(self):
        doc = frappe._dict({"company": None, SALARY_ADVANCE_FIELD: DEBTORS})
        with patch("frappe.db.get_value", side_effect=_lookup()):
            validate_pos_profile_accounts(doc, None)  # must not raise


if __name__ == "__main__":
    unittest.main()
