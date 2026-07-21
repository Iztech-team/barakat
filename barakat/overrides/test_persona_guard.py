"""Pure, Frappe-free tests for the persona-preset guard decision and the persona
bundles. Runs locally (`python -m unittest barakat.overrides.test_persona_guard`)
and under the bench test runner — it imports only `barakat.permissions`, which has
no Frappe dependency.
"""

import unittest

from barakat.permissions import STAFF_MANAGER_ROLE, may_assign_preset


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


if __name__ == "__main__":
    unittest.main()
