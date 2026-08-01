"""Pure, Frappe-free tests for "who may untick Create User Permission".

That checkbox is the tenant boundary's trigger: unticking it makes ERPNext delete
the user's `Company` User Permission, which unscopes them from every shop on the
site. `reassert_company_user_permission` already puts the row back on the same save,
so an untick is a no-op today — this guard is the second lock, so the hole cannot
reopen if that hook is ever changed.

Runs locally:  python -m unittest barakat.test_user_permission_guard
"""

import unittest

from barakat.permissions import STAFF_MANAGER_ROLE, user_permission_untick_allowed

MANAGER_ROLES = [STAFF_MANAGER_ROLE, "Barakat Staff Writer"]
OWNER_ROLES = ["System Manager"]


def _call(**overrides):
    """The guard's inputs, with the normal AP staff shape as the default."""
    kwargs = {
        "preset": "Cashier",
        "user_id": "cashier@example.com",
        "create_user_permission": 0,
        "caller_roles": MANAGER_ROLES,
    }
    kwargs.update(overrides)
    return user_permission_untick_allowed(**kwargs)


class OnlyTheOwnerMayUntick(unittest.TestCase):
    def test_manager_may_not_untick(self):
        self.assertFalse(_call())

    def test_owner_may_untick(self):
        self.assertTrue(_call(caller_roles=OWNER_ROLES))

    def test_administrator_may_untick(self):
        self.assertTrue(_call(caller_roles=[], is_administrator=True))

    def test_install_and_migrate_may_untick(self):
        self.assertTrue(_call(caller_roles=[], is_system_context=True))


class StandsDownWhenTheFlagIsIrrelevant(unittest.TestCase):
    """Anything that is not "a Barakat staff member with a login, unticked" must
    pass straight through — otherwise an unrelated save (a salary edit, an HR-owned
    employee) starts failing for a Manager who changed nothing."""

    def test_ticked_is_always_allowed(self):
        self.assertTrue(_call(create_user_permission=1))

    def test_employee_without_a_login_is_ignored(self):
        # No linked User means no User Permission exists to delete.
        self.assertTrue(_call(user_id="", create_user_permission=0))

    def test_employee_without_a_persona_is_ignored(self):
        # Not created through the admin panel; the re-assert hook skips these too.
        self.assertTrue(_call(preset="", create_user_permission=0))

    def test_unrecognised_persona_is_ignored(self):
        self.assertTrue(_call(preset="Not A Persona", create_user_permission=0))


class BlanksAreTreatedAsAbsent(unittest.TestCase):
    def test_whitespace_only_login_is_no_login(self):
        self.assertTrue(_call(user_id="   "))

    def test_whitespace_only_preset_is_no_preset(self):
        self.assertTrue(_call(preset="   "))

    def test_none_values_do_not_raise(self):
        self.assertTrue(_call(preset=None, user_id=None))


if __name__ == "__main__":
    unittest.main()
