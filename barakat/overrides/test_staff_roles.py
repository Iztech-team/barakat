"""On-bench tests for the persona-preset guard wrapper and the staff-admin role's
permissions. Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_staff_roles
Not runnable on the Windows dev box (imports `frappe`).
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.staff_roles import (
    guard_role_preset,
    reassert_company_user_permission,
)
from barakat.permissions import STAFF_MANAGER_ROLE


class _Doc:
    """Minimal Employee stand-in: the guard only reads these two members."""

    def __init__(self, preset, changed):
        self.custom_role_preset = preset
        self._changed = changed

    def has_value_changed(self, fieldname):
        return self._changed


@contextmanager
def _as(user, roles):
    saved = frappe.session.user
    frappe.session.user = user
    try:
        with patch("frappe.get_roles", return_value=roles):
            yield
    finally:
        frappe.session.user = saved


class GuardRolePreset(FrappeTestCase):
    def test_blocks_non_staff_admin(self):
        with _as("hr@example.com", ["HR Manager", "HR User"]):
            with self.assertRaises(frappe.PermissionError):
                guard_role_preset(_Doc("Accountant", changed=True))

    def test_allows_staff_admin(self):
        with _as("manager@example.com", [STAFF_MANAGER_ROLE]):
            guard_role_preset(_Doc("Cashier", changed=True))  # must not raise

    def test_allows_when_preset_unchanged(self):
        with _as("hr@example.com", ["HR Manager"]):
            guard_role_preset(_Doc("Accountant", changed=False))  # must not raise

    def test_allows_empty_preset(self):
        with _as("hr@example.com", ["HR Manager"]):
            guard_role_preset(_Doc("", changed=True))  # must not raise


class StaffManagerPerms(FrappeTestCase):
    def test_no_user_permission_grant(self):
        # After migrate, Barakat Staff Manager must hold no DocPerm on User Permission.
        rows = frappe.get_all(
            "Custom DocPerm",
            filters={"role": STAFF_MANAGER_ROLE, "parent": "User Permission"},
            pluck="name",
        )
        self.assertEqual(rows, [])

    def test_keeps_user_create(self):
        rows = frappe.get_all(
            "Custom DocPerm",
            filters={"role": STAFF_MANAGER_ROLE, "parent": "User"},
            pluck="name",
        )
        self.assertTrue(rows)


class ReassertCompanyUserPermission(FrappeTestCase):
    """The tenant boundary must not depend on Employee.create_user_permission.

    ERPNext deletes the Company User Permission when that checkbox is unticked, and
    its label only mentions employee records — so the company wall can be dropped by
    accident. This hook re-asserts it on every save, add-only.
    """

    def _emp(self, preset="Cashier", user="staff@example.com", company="ACME"):
        return frappe._dict(
            {"custom_role_preset": preset, "user_id": user, "company": company}
        )

    def _exists(self, permission_present):
        def side_effect(doctype, *args, **kwargs):
            if doctype == "User":
                return True
            if doctype == "User Permission":
                return permission_present
            return False

        return side_effect

    def test_creates_the_permission_when_missing(self):
        with patch("frappe.db.exists", side_effect=self._exists(False)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp())
        add.assert_called_once_with("Company", "ACME", "staff@example.com")

    def test_is_a_noop_when_already_present(self):
        with patch("frappe.db.exists", side_effect=self._exists(True)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp())
        add.assert_not_called()

    def test_skips_unrecognised_preset(self):
        with patch("frappe.db.exists", side_effect=self._exists(False)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp(preset="Not A Persona"))
        add.assert_not_called()

    def test_skips_when_no_login_or_no_company(self):
        with patch("frappe.db.exists", side_effect=self._exists(False)), patch(
            "frappe.permissions.add_user_permission"
        ) as add:
            reassert_company_user_permission(self._emp(user=""))
            reassert_company_user_permission(self._emp(company=""))
        add.assert_not_called()

    def test_never_removes_a_permission(self):
        """Add-only: an area manager's hand-granted second company must survive."""
        with patch("frappe.db.exists", side_effect=self._exists(True)), patch(
            "frappe.permissions.remove_user_permission"
        ) as remove:
            reassert_company_user_permission(self._emp())
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
