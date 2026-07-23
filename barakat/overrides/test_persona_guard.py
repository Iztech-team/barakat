"""Pure, Frappe-free tests for the persona-preset guard decision and the persona
bundles. Runs locally (`python -m unittest barakat.overrides.test_persona_guard`)
and under the bench test runner — it imports only `barakat.permissions`, which has
no Frappe dependency.
"""

import unittest

from barakat import hooks
from barakat.permissions import (
    BARAKAT_ROLE_PERMS,
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


class RoleFixtureCoverage(unittest.TestCase):
    """Every Barakat role a bundle or DocPerm map names must also be exported by the
    `Role` fixture in hooks.py.

    `staff_roles.persona_role_bundle` intersects a bundle with the roles that exist on
    the site, so a role this app never ships is silently dropped — the user ends up
    with FEWER roles and no error. Fails closed, but still a functional bug.
    """

    def _fixture_roles(self):
        entry = next(
            f for f in hooks.fixtures if isinstance(f, dict) and f.get("dt") == "Role"
        )
        return set(entry["filters"][0][2])

    def test_every_referenced_barakat_role_is_exported(self):
        referenced = set()
        for roles in PERSONA_ROLE_BUNDLES.values():
            referenced |= set(roles)
        referenced |= set(BARAKAT_ROLE_PERMS)
        missing = sorted(
            r
            for r in referenced
            if r.startswith("Barakat") and r not in self._fixture_roles()
        )
        self.assertEqual(missing, [], f"missing from the hooks.py Role fixture: {missing}")


if __name__ == "__main__":
    unittest.main()
