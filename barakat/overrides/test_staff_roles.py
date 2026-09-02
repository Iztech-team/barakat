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
    guard_user_permission_flag,
    reassert_company_user_permission,
    sign_out_on_access_removed,
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


class GuardUserPermissionFlag(FrappeTestCase):
    """Only the owner may save a persona staff member with the company restriction off.

    The decision and its stand-downs are covered off-bench in
    `barakat.test_user_permission_guard`; this pins the Frappe wiring — that the
    wrapper reads the right fields off the doc and raises PermissionError.
    """

    def _emp(self, ticked=0, preset="Cashier", user="staff@example.com"):
        return frappe._dict(
            {
                "custom_role_preset": preset,
                "user_id": user,
                "create_user_permission": ticked,
            }
        )

    def test_blocks_the_manager(self):
        with _as("manager@example.com", [STAFF_MANAGER_ROLE]):
            with self.assertRaises(frappe.PermissionError):
                guard_user_permission_flag(self._emp(ticked=0))

    def test_allows_the_owner(self):
        with _as("owner@example.com", ["System Manager"]):
            guard_user_permission_flag(self._emp(ticked=0))  # must not raise

    def test_allows_the_manager_when_ticked(self):
        """The ordinary staff create/edit path must be untouched."""
        with _as("manager@example.com", [STAFF_MANAGER_ROLE]):
            guard_user_permission_flag(self._emp(ticked=1))  # must not raise

    def test_ignores_an_employee_with_no_login(self):
        with _as("manager@example.com", [STAFF_MANAGER_ROLE]):
            guard_user_permission_flag(self._emp(ticked=0, user=""))  # must not raise


class _SavedEmployee:
    """Employee stand-in for the sign-out hook: the fields it reads, before and after."""

    def __init__(self, before, **now):
        self._before = _SavedEmployee(None, **before) if before is not None else None
        self.user_id = now.get("user_id", "keeper@example.com")
        self.custom_role_preset = now.get("custom_role_preset", "Inventory Keeper")
        self.status = now.get("status", "Active")

    def get_doc_before_save(self):
        return self._before


UNCHANGED = {
    "user_id": "keeper@example.com",
    "custom_role_preset": "Inventory Keeper",
    "status": "Active",
}


class SignOutOnAccessRemoved(FrappeTestCase):
    """QA 0001-607 — a staff member whose access is taken away loses their session NOW.

    The proxy re-reads the persona when a token is refreshed, but that only bites
    within the access token's lifetime and only in the process holding the list.
    Dropping the ERPNext session here is what makes a demotion or an offboarding
    take effect immediately, for every process, across a restart.
    """

    def _run(self, before=UNCHANGED, **now):
        with patch("frappe.sessions.clear_sessions") as cleared:
            sign_out_on_access_removed(_SavedEmployee(before, **now))
        return cleared

    def test_demotion_drops_the_session(self):
        cleared = self._run(custom_role_preset="Cashier")
        cleared.assert_called_once_with(user="keeper@example.com", force=True)

    def test_offboarding_drops_the_session(self):
        cleared = self._run(status="Left")
        cleared.assert_called_once_with(user="keeper@example.com", force=True)

    def test_moving_the_login_drops_the_OLD_address(self):
        cleared = self._run(user_id="new.address@example.com")
        cleared.assert_called_once_with(user="keeper@example.com", force=True)

    def test_an_unrelated_edit_leaves_the_session_alone(self):
        self._run().assert_not_called()

    def test_an_insert_has_nothing_to_drop(self):
        self._run(before=None, custom_role_preset="Cashier").assert_not_called()

    def test_the_administrator_is_never_signed_out(self):
        # Locking the site owner out of their own bench is not a fix.
        cleared = self._run(
            before={**UNCHANGED, "user_id": "Administrator"},
            user_id="Administrator",
            custom_role_preset="Cashier",
        )
        cleared.assert_not_called()

    def test_a_migration_signs_nobody_out(self):
        # `reassert_persona_roles` rewrites bundles during install/migrate; a save
        # made by the system must never log the whole shop out.
        frappe.flags.in_migrate = True
        try:
            self._run(custom_role_preset="Cashier").assert_not_called()
        finally:
            frappe.flags.in_migrate = False


if __name__ == "__main__":
    unittest.main()
