"""Pure, Frappe-free tests for the persona-preset guard decision and the persona
bundles. Runs locally (`python -m unittest barakat.overrides.test_persona_guard`)
and under the bench test runner — it imports only `barakat.permissions`, which has
no Frappe dependency.
"""

import unittest

from barakat.permissions import (
    FORBIDDEN_ROLES,
    PERSONA_ROLE_BUNDLES,
    STAFF_MANAGER_ROLE,
    bundle_for,
    may_assign_preset,
)


class MayAssignPreset(unittest.TestCase):
    def test_staff_admin_may_assign(self):
        self.assertTrue(may_assign_preset([STAFF_MANAGER_ROLE, "HR User"]))

    def test_system_manager_may_assign(self):
        self.assertTrue(may_assign_preset(["System Manager"]))

    def test_plain_caller_may_not_assign(self):
        self.assertFalse(may_assign_preset(["HR Manager", "HR User"]))

    def test_no_roles_may_not_assign(self):
        self.assertFalse(may_assign_preset([]))

    def test_administrator_bypasses(self):
        self.assertTrue(may_assign_preset([], is_administrator=True))

    def test_system_context_bypasses(self):
        self.assertTrue(may_assign_preset([], is_system_context=True))


class HrBundleNoLongerStaffAdmin(unittest.TestCase):
    def test_hr_has_no_staff_admin_role(self):
        self.assertNotIn(STAFF_MANAGER_ROLE, bundle_for("HR"))

    def test_hr_keeps_payroll_roles(self):
        hr = bundle_for("HR")
        self.assertIn("HR Manager", hr)
        self.assertIn("HR User", hr)

    def test_manager_still_staff_admin(self):
        self.assertIn(STAFF_MANAGER_ROLE, bundle_for("Manager"))

    def test_no_bundle_leaks_forbidden_role(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            self.assertEqual(FORBIDDEN_ROLES.intersection(roles), set(), persona)


if __name__ == "__main__":
    unittest.main()
