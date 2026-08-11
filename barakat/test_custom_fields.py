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

    def test_the_2026_08_05_leak_markers_are_shipped(self):
        # Contact, Item Price and Product Bundle had NO company field of any kind, so
        # the Company user permission had no link field to bind to and every persona
        # read every shop's rows. Measured on prod: a Cashier of one shop saw all 405
        # contacts (271 with a mobile number) and all 34,719 item prices.
        for name in (
            "Contact-custom_company",
            "Item Price-custom_company",
            "Product Bundle-custom_company",
        ):
            with self.subTest(field=name):
                f = _by_name(self.rows, name)
                self.assertIsNotNone(f, f"{name} missing from fixtures")
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


class CashierLimitFieldsAreDeclared(unittest.TestCase):
    """The three per-till limits the POS Profile carries.

    See docs/superpowers/specs/2026-08-11-pos-cashier-limits-design.md. The
    defaults are the load-bearing part: a max-discount default of 0 would make
    pos_invoice.py reject every discounted sale at every shop.
    """

    def setUp(self):
        self.rows = _rows()

    def test_section_break_exists_after_bank_account(self):
        f = _by_name(self.rows, "POS Profile-custom_cashier_limits_section")
        self.assertIsNotNone(f, "cashier limits section missing from fixtures")
        self.assertEqual(f["fieldtype"], "Section Break")
        self.assertEqual(f["insert_after"], "custom_bank_account")

    def test_both_toggles_are_checks_defaulting_off(self):
        for fieldname in ("custom_allow_ad_hoc_item", "custom_allow_customer_creation"):
            f = _by_name(self.rows, f"POS Profile-{fieldname}")
            self.assertIsNotNone(f, f"{fieldname} missing from fixtures")
            self.assertEqual(f["fieldtype"], "Check", fieldname)
            # A Check with no default is 0 anyway, but state it so the intent
            # survives an edit: the client asked for these OFF by default.
            self.assertEqual(f.get("default", "0"), "0", fieldname)

    def test_max_discount_is_percent_defaulting_to_100(self):
        f = _by_name(self.rows, "POS Profile-custom_max_discount_percent")
        self.assertIsNotNone(f, "custom_max_discount_percent missing from fixtures")
        self.assertEqual(f["fieldtype"], "Percent")
        # 100, never 0. A 0 default would make pos_invoice.py reject every
        # discounted sale at every shop the moment this ships.
        self.assertEqual(f["default"], "100")


class CustomerPosProfileStamp(unittest.TestCase):
    def setUp(self):
        self.rows = _rows()

    def test_stamp_is_data_not_link(self):
        f = _by_name(self.rows, "Customer-custom_pos_profile")
        self.assertIsNotNone(f, "Customer-custom_pos_profile missing from fixtures")
        # Data, not Link: a Link would make a POS Profile undeletable once a till
        # had created a customer under it (LinkExistsError).
        self.assertEqual(f["fieldtype"], "Data")
        self.assertEqual(f.get("read_only"), 1)


if __name__ == "__main__":
    unittest.main()
