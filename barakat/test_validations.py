"""On-bench tests for the rules in `barakat.validations`.

Run on a site:
    bench --site <site> run-tests --module barakat.test_validations
Not runnable on the Windows dev box (imports `frappe`).

These stub the database lookups rather than depending on a real chart of accounts
or a real employee roster, so they assert the RULE, not one site's data.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.validations import (
    SALARY_ADVANCE_FIELD,
    validate_employee_pin,
    validate_loyalty_program_tier_names,
    validate_pos_profile_accounts,
)

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
    def test_allows_the_company_default_receivable(self):
        """Deliberate: "Debtors" is accepted, and this test exists to keep it that way.

        A guard here used to reject the company's default receivable, because a staff
        advance posted to it lands in CUSTOMER receivables and skews AR aging (report
        item HIGH 4). The owner removed that on 2026-07-23: we enforce the account
        TYPE, and which receivable account to use is the accountant's decision.

        This asserts the absence of the rule on purpose. If someone re-adds the guard
        to "fix AR aging", this test fails and points them at that decision instead of
        letting the rule quietly come back.
        """
        with patch("frappe.db.get_value", side_effect=_lookup()):
            validate_pos_profile_accounts(_doc(DEBTORS), None)  # must not raise

    def test_allows_a_dedicated_advances_account(self):
        with patch("frappe.db.get_value", side_effect=_lookup()):
            validate_pos_profile_accounts(
                _doc("Employee Advances - TC"), None
            )  # must not raise

    def test_still_rejects_a_non_receivable_account(self):
        """The account-TYPE rule is what we do still enforce."""
        with patch("frappe.db.get_value", side_effect=_lookup(account_type="Bank")):
            with self.assertRaises(frappe.ValidationError):
                validate_pos_profile_accounts(_doc("Some Bank - TC"), None)

    def test_no_company_on_the_profile_does_not_crash(self):
        doc = frappe._dict({"company": None, SALARY_ADVANCE_FIELD: DEBTORS})
        with patch("frappe.db.get_value", side_effect=_lookup()):
            validate_pos_profile_accounts(doc, None)  # must not raise


class LoyaltyProgramTierNames(FrappeTestCase):
    """The rule that keeps a program's tier names distinct.

    Duplicate tier names are legal in ERPNext and fatal to the POS, whose local
    tier table is keyed on (site_url, program, tier_name).
    """

    def _doc(self, *names):
        return frappe._dict(
            {"collection_rules": [frappe._dict({"tier_name": n}) for n in names]}
        )

    def test_distinct_names_save(self):
        validate_loyalty_program_tier_names(self._doc("Bronze", "Gold"), "validate")

    def test_exact_duplicate_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            validate_loyalty_program_tier_names(
                self._doc("vip vip", "vip vip"), "validate"
            )

    def test_case_or_space_only_difference_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            validate_loyalty_program_tier_names(self._doc("VIP", " vip "), "validate")

    def test_blank_name_does_not_trip_this_rule(self):
        validate_loyalty_program_tier_names(self._doc("", ""), "validate")

    def test_program_with_no_tiers_is_fine(self):
        validate_loyalty_program_tier_names(frappe._dict({}), "validate")


class EmployeePinUniqueness(FrappeTestCase):
    """A POS PIN must be unique per COMPANY — never merely per branch or per site.

    A company is reachable from an Employee by two different links, and the two
    layers that guard the PIN each used to read only one of them:

      * the admin panel's proxy filters on `Employee.company`
      * this rule read `Branch.custom_pos_company`

    Two holes came out of that split, and both are pinned below: a branchless
    employee escaped this rule entirely (`test_branchless_*`), and a record whose
    two links named different companies got a different answer from each layer
    (`test_*_both_links`). The fix searches the union of both links.
    """

    BRANCH_CO = "Barakat Nablus"
    EMP_CO = "Barakat Ramallah"

    def _doc(self, pin="1234", company=None, branch=None, name="HR-EMP-00001"):
        return frappe._dict(
            {
                "name": name,
                "custom_pos_pin": pin,
                "company": company,
                "branch": branch,
                "employee_name": "Test Person",
            }
        )

    def _run(self, doc, duplicates=None, branch_company=None, stored=None):
        """Run the validator; return the params of the duplicate query, or None.

        None means the validator never reached the database — which is the exact
        failure hole 1 was, so several tests below assert it is NOT None.

        `stored` is the employee's row as it exists BEFORE this save (None for a
        new one), which is what the "only judge what changed" guard reads.
        """
        calls = []

        def fake_sql(query, params=None, **kwargs):
            calls.append(params)
            return list(duplicates or [])

        def fake_get_value(doctype, name, fields=None, **kwargs):
            if doctype == "Branch":
                return branch_company
            if doctype == "Employee":
                return frappe._dict(stored) if stored else None
            return None

        with (
            patch("frappe.db.sql", side_effect=fake_sql),
            patch("frappe.db.get_value", side_effect=fake_get_value),
        ):
            validate_employee_pin(doc, "validate")

        return calls[0] if calls else None

    def _stored(self, pin="1234", company=None, branch=None):
        return {"custom_pos_pin": pin, "company": company, "branch": branch}

    def _dupe(self, emp_company=None, branch_company=None):
        return {
            "name": "HR-EMP-00099",
            "employee_name": "Someone Else",
            "emp_company": emp_company,
            "branch_company": branch_company,
        }

    # --- format -----------------------------------------------------------

    def test_rejects_a_pin_that_is_not_4_to_6_digits(self):
        for bad in ("12a4", "123", "1234567", "12 34"):
            with self.subTest(pin=bad):
                with self.assertRaises(frappe.ValidationError):
                    self._run(self._doc(pin=bad, company=self.EMP_CO))

    def test_no_pin_skips_the_check_entirely(self):
        self.assertIsNone(self._run(self._doc(pin="", company=self.EMP_CO)))

    # --- hole 1: the branchless employee ----------------------------------

    def test_branchless_employee_is_still_checked(self):
        """Used to `return` before the query — two branchless staff could share a PIN."""
        params = self._run(self._doc(company=self.EMP_CO, branch=None))
        self.assertIsNotNone(params)
        self.assertIn(self.EMP_CO, params)

    def test_branchless_duplicate_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._run(
                self._doc(company=self.EMP_CO, branch=None),
                duplicates=[self._dupe(emp_company=self.EMP_CO)],
            )

    def test_no_company_link_at_all_lets_the_save_through(self):
        """Nothing to scope against: refusing here would block legitimate saves."""
        self.assertIsNone(self._run(self._doc(company=None, branch=None)))

    def test_a_branch_with_no_company_falls_back_to_the_employees_own(self):
        params = self._run(
            self._doc(company=self.EMP_CO, branch="Some Branch"),
            branch_company=None,
        )
        self.assertIsNotNone(params)
        self.assertIn(self.EMP_CO, params)

    # --- hole 2: the two links disagreeing --------------------------------

    def test_searches_both_links(self):
        params = self._run(
            self._doc(company=self.EMP_CO, branch="Nablus Main"),
            branch_company=self.BRANCH_CO,
        )
        self.assertIsNotNone(params)
        self.assertIn(self.EMP_CO, params)
        self.assertIn(self.BRANCH_CO, params)

    def test_duplicate_reached_by_the_branch_link_is_rejected(self):
        """The clash is in the BRANCH's company, which the old proxy filter missed."""
        with self.assertRaises(frappe.ValidationError):
            self._run(
                self._doc(company=self.EMP_CO, branch="Nablus Main"),
                branch_company=self.BRANCH_CO,
                duplicates=[self._dupe(branch_company=self.BRANCH_CO)],
            )

    def test_the_message_gives_nothing_away(self):
        """The duplicate error must not be usable as a PIN oracle.

        It used to read "PIN <b>4471</b> is already assigned to <b>Ahmad</b> in
        company <b>X</b>", which turned the employee form into a lookup service:
        type a candidate PIN, and be told whose it is. Anyone who could edit one
        employee could find a manager's PIN that way, with no database access.
        """
        with self.assertRaises(frappe.ValidationError) as caught:
            self._run(
                self._doc(company=self.EMP_CO, branch="Nablus Main"),
                branch_company=self.BRANCH_CO,
                duplicates=[
                    self._dupe(
                        emp_company="Unrelated Co", branch_company=self.BRANCH_CO
                    )
                ],
            )

        message = str(caught.exception)
        self.assertNotIn("1234", message)
        self.assertNotIn("Someone Else", message)
        self.assertNotIn("HR-EMP-00099", message)
        self.assertNotIn(self.BRANCH_CO, message)
        self.assertNotIn("Unrelated Co", message)
        # It still has to be useful to the person choosing a PIN.
        self.assertIn("already in use", message)

    # --- editing an existing employee -------------------------------------

    def test_the_employee_being_edited_is_excluded(self):
        """Re-saving someone with their OWN unchanged PIN must stay legal."""
        params = self._run(self._doc(company=self.EMP_CO, name="HR-EMP-00007"))
        self.assertIn("HR-EMP-00007", params)

    def test_a_brand_new_employee_excludes_a_sentinel_not_an_empty_name(self):
        """`name` is empty before insert; an empty exclusion would match nothing."""
        params = self._run(self._doc(company=self.EMP_CO, name=""))
        self.assertIn("__new__", params)

    # --- only judging what the save actually changed -----------------------

    def test_an_untouched_pin_does_not_re_run_the_rule(self):
        """Editing a salary must not fail over a PIN this save never touched.

        The rule is stricter than the one some live records were saved under, so
        re-deciding an unchanged PIN would retroactively freeze those employees.
        """
        doc = self._doc(pin="1234", company=self.EMP_CO, branch="Nablus Main")
        params = self._run(
            doc,
            branch_company=self.BRANCH_CO,
            stored=self._stored(
                pin="1234", company=self.EMP_CO, branch="Nablus Main"
            ),
            duplicates=[self._dupe(emp_company=self.EMP_CO)],
        )
        self.assertIsNone(params)

    def test_a_legacy_malformed_pin_does_not_block_an_unrelated_edit(self):
        doc = self._doc(pin="12", company=self.EMP_CO)
        self._run(doc, stored=self._stored(pin="12", company=self.EMP_CO))

    def test_changing_the_pin_is_judged(self):
        doc = self._doc(pin="5678", company=self.EMP_CO)
        with self.assertRaises(frappe.ValidationError):
            self._run(
                doc,
                stored=self._stored(pin="1234", company=self.EMP_CO),
                duplicates=[self._dupe(emp_company=self.EMP_CO)],
            )

    def test_moving_to_another_branch_is_judged_even_with_the_same_pin(self):
        """A clash can be created by the MOVE alone — the PIN need not change."""
        doc = self._doc(pin="1234", company=self.EMP_CO, branch="Hebron Main")
        with self.assertRaises(frappe.ValidationError):
            self._run(
                doc,
                branch_company=self.BRANCH_CO,
                stored=self._stored(
                    pin="1234", company=self.EMP_CO, branch="Nablus Main"
                ),
                duplicates=[self._dupe(branch_company=self.BRANCH_CO)],
            )

    def test_moving_to_another_company_is_judged_even_with_the_same_pin(self):
        doc = self._doc(pin="1234", company="Barakat Hebron")
        with self.assertRaises(frappe.ValidationError):
            self._run(
                doc,
                stored=self._stored(pin="1234", company=self.EMP_CO),
                duplicates=[self._dupe(emp_company="Barakat Hebron")],
            )


if __name__ == "__main__":
    unittest.main()
