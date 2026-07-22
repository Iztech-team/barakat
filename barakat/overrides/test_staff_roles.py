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

from barakat.overrides.staff_roles import guard_role_preset
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


if __name__ == "__main__":
    unittest.main()
