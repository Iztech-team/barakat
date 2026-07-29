"""Pure, Frappe-free guard on the company/branch marker custom fields.

Frappe enforces user permissions by scanning a doctype's LINK fields and matching
each field's `options` against the user's permissions
(frappe/model/db_query.py::add_user_permissions). A `Data` marker is not a link
field, so it is invisible to that scan and CANNOT be enforced — which is exactly
how 2,326 customers stayed readable across companies. This test makes that
mistake impossible to reintroduce.

Runs locally:  python -m unittest barakat.test_custom_fields
"""

import json
import pathlib
import unittest

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "custom_field.json"


def _rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_name(rows, name):
    return next((f for f in rows if f.get("name") == name), None)


class CompanyMarkersAreEnforceable(unittest.TestCase):
    def setUp(self):
        self.rows = _rows()

    def test_no_company_or_branch_marker_is_data(self):
        offenders = sorted(
            f"{f.get('dt')}.{f.get('fieldname')}"
            for f in self.rows
            if f.get("fieldtype") == "Data"
            and (
                "company" in str(f.get("fieldname", "")).lower()
                or "branch" in str(f.get("fieldname", "")).lower()
            )
        )
        self.assertEqual(
            offenders,
            [],
            f"Data markers can never be enforced by user permissions: {offenders}",
        )

    def test_customer_company_links_to_company(self):
        f = _by_name(self.rows, "Customer-custom_company")
        self.assertIsNotNone(f, "Customer-custom_company missing from fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Company")

    def test_customer_branch_links_to_branch(self):
        f = _by_name(self.rows, "Customer-custom_branch")
        self.assertIsNotNone(f, "Customer-custom_branch missing from fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Branch")

    def test_customer_group_company_is_shipped(self):
        # Exists on live sites as a proper Link, but was created by hand and never
        # added to the fixtures — a fresh site would come up without it and customer
        # groups would silently stop being scoped.
        f = _by_name(self.rows, "Customer Group-custom_company")
        self.assertIsNotNone(f, "Customer Group-custom_company is not shipped in fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Company")

    def test_uom_has_enforceable_company_marker(self):
        f = _by_name(self.rows, "UOM-custom_company")
        self.assertIsNotNone(f, "UOM must carry a custom_company marker")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "Company")

    def test_company_scale_uom_links_to_uom(self):
        f = _by_name(self.rows, "Company-custom_scale_uom")
        self.assertIsNotNone(f, "Company-custom_scale_uom missing from fixtures")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "UOM")

    def test_company_carries_the_chart_of_accounts_language(self):
        """The proxy sets this on the Company INSERT and BarakatCompany reads it
        during that same save to pick the chart's language. If the field is not
        installed, Frappe silently drops the value and the shop is created with
        ERPNext's English chart — whose account ids can never be renamed. The
        proxy pins the same string in coa-language.test.ts.
        """
        f = _by_name(self.rows, "Company-custom_barakat_coa_language")
        self.assertIsNotNone(f, "Company-custom_barakat_coa_language missing from fixtures")
        self.assertEqual(f["fieldname"], "custom_barakat_coa_language")
        self.assertEqual(f["fieldtype"], "Select")
        self.assertEqual(f["options"], "\nar\nhe\nen")


class ClosingEntryRecordsBothIdentities(unittest.TestCase):
    """A shift's closing side must name the PIN staff AND the signing account.

    ERPNext's own `user` field is a read-only fetch from the opening entry, so it
    always names the OPENER. Without custom_closed_by_user there is no record
    anywhere of which account actually performed the close — which matters now
    that any signed-in account may close a shift on its till.
    """

    def setUp(self):
        self.rows = _rows()

    def test_closed_by_user_field_exists(self):
        f = _by_name(self.rows, "POS Closing Entry-custom_closed_by_user")
        self.assertIsNotNone(f, "POS Closing Entry-custom_closed_by_user is missing")

    def test_closed_by_user_is_a_link_to_user(self):
        f = _by_name(self.rows, "POS Closing Entry-custom_closed_by_user")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "User")
        self.assertEqual(f["dt"], "POS Closing Entry")

    def test_closed_by_staff_still_exists(self):
        f = _by_name(self.rows, "POS Closing Entry-custom_closed_by_staff")
        self.assertIsNotNone(f, "POS Closing Entry-custom_closed_by_staff is missing")
        self.assertEqual(f["options"], "Employee")


if __name__ == "__main__":
    unittest.main()
