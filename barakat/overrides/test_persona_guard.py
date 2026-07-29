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
    SUPPLIER_LEDGER_ROLE,
    bundle_for,
    gl_entry_scope_for,
    may_assign_preset,
)
from barakat.persona_matrix import (
    MODULE_DOCTYPES,
    MODULE_KEYS,
    PERSONA_MATRIX,
    TILL_REQUIRED_READS,
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


class SupplierLedgerRole(unittest.TestCase):
    """The Inventory Keeper's supplier statement (`reports.suppliers: read`) reads
    GL Entry, which no other role in its bundle carries — the page failed with
    "error loading data" until this role existed. The scope helper is what keeps the
    grant to supplier rows instead of the whole ledger.
    """

    def test_inventory_keeper_holds_the_role(self):
        self.assertIn(SUPPLIER_LEDGER_ROLE, bundle_for("Inventory Keeper"))

    def test_role_grants_gl_entry_read_only(self):
        self.assertEqual(BARAKAT_ROLE_PERMS[SUPPLIER_LEDGER_ROLE], {"GL Entry": ("read",)})

    def test_holder_is_scoped_to_supplier_rows(self):
        self.assertEqual(
            gl_entry_scope_for(["Stock Manager", SUPPLIER_LEDGER_ROLE]), "supplier"
        )

    def test_native_accounts_holder_is_not_scoped(self):
        self.assertIsNone(gl_entry_scope_for(["Accounts User", SUPPLIER_LEDGER_ROLE]))

    def test_caller_without_the_role_is_not_scoped(self):
        self.assertIsNone(gl_entry_scope_for(["Stock Manager"]))
        self.assertIsNone(gl_entry_scope_for([]))

    def test_personas_without_the_supplier_report_do_not_hold_it(self):
        for persona in ("Cashier", "HR", "Branch Supervisor"):
            self.assertNotIn(SUPPLIER_LEDGER_ROLE, bundle_for(persona), persona)


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


class PersonaMatrixData(unittest.TestCase):
    """The Python twin of proxy-barakat/src/modules/roles/catalog.ts."""

    def test_every_module_key_has_a_doctype_list(self):
        for key in MODULE_KEYS:
            self.assertIn(key, MODULE_DOCTYPES, key)

    def test_every_persona_covers_every_module(self):
        for persona, row in PERSONA_MATRIX.items():
            for key in MODULE_KEYS:
                self.assertIn(key, row, f"{persona} missing {key}")
                self.assertIn(row[key], ("none", "read", "write"), f"{persona}.{key}")

    def test_personas_match_the_bundle_keys(self):
        self.assertEqual(set(PERSONA_MATRIX), set(PERSONA_ROLE_BUNDLES))

    def test_till_reads_are_declared(self):
        # The till pulls these under a Manager / Branch Supervisor device session.
        for doctype in ("System Settings", "Global Defaults", "Device", "POS Scale Settings"):
            self.assertIn(doctype, TILL_REQUIRED_READS, doctype)

    def test_matrix_matches_the_json_snapshot(self):
        import json
        import os

        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "persona_matrix.json")
        with open(path, encoding="utf-8") as fh:
            snapshot = json.load(fh)
        self.assertEqual(snapshot, PERSONA_MATRIX)


if __name__ == "__main__":
    unittest.main()
