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


if __name__ == "__main__":
    unittest.main()
