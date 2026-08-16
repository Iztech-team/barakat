"""The POS PIN field sits behind permlevel 1, and exactly two personas hold the key.

These are structural assertions, not behavioural ones — the behaviour was proven on a
real bench (a role without the grant gets `None` for `custom_pos_pin` from both
`/api/resource/Employee/<name>` and a list asking for the field by name; a role with it
reads the PIN and, critically, still SAVES it rather than having it silently dropped).

What these tests defend is the wiring that makes that true, because every part of it is
easy to undo by accident: drop the role from a bundle and a whole shop's tills stop
authenticating cashiers; drop the field's permlevel and the leak reopens with no test
failing anywhere.
"""

import json
import unittest
from pathlib import Path

from barakat.permissions import (
    EXPORTED_ROLE_NAMES,
    EXTERNALLY_PERMED_ROLES,
    PERSONA_ROLE_BUNDLES,
)

ROLE = "Barakat POS PIN Reader"
FIXTURES = Path(__file__).parent / "fixtures" / "custom_field.json"


def _pin_field():
    fields = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for f in fields:
        if f.get("name") == "Employee-custom_pos_pin":
            return f
    raise AssertionError("Employee-custom_pos_pin is missing from the fixture")


class PosPinFieldIsProtected(unittest.TestCase):
    def test_the_field_is_at_permlevel_1(self):
        # permlevel 0 means every role that can read an Employee can read the
        # credential — which is how HR, Attendance Manager and Salary Viewer all had
        # every PIN in the company off one /api/resource call.
        self.assertEqual(_pin_field().get("permlevel"), 1)

    def test_the_field_is_not_copied_on_duplicate(self):
        self.assertEqual(_pin_field().get("no_copy"), 1)


class OnlyTillPersonasHoldTheKey(unittest.TestCase):
    def test_manager_and_branch_supervisor_have_it(self):
        # Manager sets PINs from the admin panel. Branch Supervisor's till pulls the
        # branch's PINs so cashiers can punch in with no network. Remove either and
        # that shop breaks.
        for persona in ("Manager", "Branch Supervisor"):
            with self.subTest(persona=persona):
                self.assertIn(ROLE, PERSONA_ROLE_BUNDLES[persona])

    def test_nobody_else_has_it(self):
        # Especially HR: it holds `staff: read`, the same generated role as Branch
        # Supervisor, which is the whole reason this had to be its own role instead
        # of a grant on the staff reader.
        for persona, bundle in PERSONA_ROLE_BUNDLES.items():
            if persona in ("Manager", "Branch Supervisor"):
                continue
            with self.subTest(persona=persona):
                self.assertNotIn(ROLE, bundle)

    def test_the_role_is_exported_as_a_fixture(self):
        # A role this app fails to export is SILENTLY dropped on other sites:
        # persona_role_bundle() intersects the bundle with the roles that exist, so
        # the user simply ends up without it and nothing anywhere says so.
        self.assertIn(ROLE, EXPORTED_ROLE_NAMES)

    def test_it_is_declared_as_externally_permed(self):
        # Its DocPerm is permlevel 1, and the ALL_ROLE_PERMS loop only ever grants
        # permlevel 0 — so it must be declared here or the guard test that every
        # bundle role is a known role will fail.
        self.assertIn(ROLE, EXTERNALLY_PERMED_ROLES)


class TheGrantRunsOnBothPaths(unittest.TestCase):
    def test_install_and_migrate_both_grant_it(self):
        # A fresh site gets permlevel 1 from the fixture. Without the grant running
        # on install too, that site would hide the field from its own Manager.
        src = (Path(__file__).parent / "setup" / "install.py").read_text(encoding="utf-8")
        after_install = src.split("def after_setup_wizard")[0]
        after_migrate = src.split("def after_migrate")[1].split("def backfill_persona_roles")[0]
        self.assertIn("_grant_pos_pin_permlevel", after_install)
        self.assertIn("_grant_pos_pin_permlevel", after_migrate)


if __name__ == "__main__":
    unittest.main()
