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
    MODULE_ROLE_PERMS,
    PRESERVED_ROLES,
    READER_PERMS,
    SELF_SERVICE_ROLE,
    WRITER_PERMS,
    self_scope_applies,
    bundle_for,
    gl_entry_scope_for,
    may_assign_preset,
    role_name_for,
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

    def test_hr_keeps_its_remaining_capabilities(self):
        # Was "keeps HR Manager / HR User". Those native roles are gone by design as of
        # 2026-07-29 — they carried unscoped Employee and Salary Slip read for every
        # persona that held them. HR keeps the CAPABILITY through the generated roles.
        #
        # This used to assert `Barakat Salary Writer` too. Payroll AUTHORING left this
        # bundle on 2026-08-02 by the owner's decision — see
        # test_payroll_authoring_belongs_to_the_accountant, which guards the trade in
        # both directions. HR still records the attendance payroll is computed from.
        hr = bundle_for("HR")
        self.assertIn("Barakat Attendance Writer", hr)
        self.assertIn("Barakat Staff Reader", hr)

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


class GeneratedModuleRoles(unittest.TestCase):
    def test_role_naming(self):
        self.assertEqual(role_name_for("products", "write"), "Barakat Products Writer")
        self.assertEqual(role_name_for("products", "read"), "Barakat Products Reader")

    def test_dotted_module_naming(self):
        self.assertEqual(role_name_for("reports.salary", "read"), "Barakat Reports Salary Reader")

    def test_none_grants_no_role(self):
        self.assertIsNone(role_name_for("products", "none"))

    def test_module_without_doctypes_grants_no_role(self):
        # `dashboard`, `reports` and `roles` are AP-only gates with no ERPNext doctype.
        self.assertIsNone(role_name_for("dashboard", "write"))
        self.assertIsNone(role_name_for("reports", "read"))
        self.assertIsNone(role_name_for("roles", "read"))

    def test_reader_grants_select_alongside_read(self):
        # Frappe's list query accepts `select` OR `read`; link pickers run on `select`
        # alone. Losing it empties dropdowns with no error anywhere.
        self.assertIn("select", READER_PERMS)
        self.assertEqual(set(MODULE_ROLE_PERMS["Barakat Products Reader"]["Item"]), set(READER_PERMS))

    def test_writer_grants_the_full_lifecycle(self):
        self.assertEqual(set(MODULE_ROLE_PERMS["Barakat Products Writer"]["Item"]), set(WRITER_PERMS))

    def test_generated_roles_cover_every_module_with_doctypes(self):
        for key, doctypes in MODULE_DOCTYPES.items():
            if not doctypes:
                continue
            self.assertIn(role_name_for(key, "read"), MODULE_ROLE_PERMS, key)
            self.assertIn(role_name_for(key, "write"), MODULE_ROLE_PERMS, key)

    def test_generated_names_never_collide_with_hand_written_roles(self):
        self.assertEqual(set(BARAKAT_ROLE_PERMS).intersection(MODULE_ROLE_PERMS), set())


NATIVE_ROLE_MARKERS = (
    "Accounts Manager", "Accounts User", "Sales Manager", "Sales Master Manager",
    "Sales User", "Stock Manager", "Stock User", "Item Manager", "Purchase Manager",
    "Purchase Master Manager", "Purchase User", "HR Manager", "HR User",
    "Employee", "Employee Self Service",
)


class BundlesDerivedFromMatrix(unittest.TestCase):
    def test_no_native_role_in_any_bundle(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            leaked = set(roles).intersection(NATIVE_ROLE_MARKERS)
            self.assertEqual(leaked, set(), f"{persona} still holds native roles: {sorted(leaked)}")

    def test_bundle_is_the_matrix_row(self):
        for persona, row in PERSONA_MATRIX.items():
            expected = {role_name_for(m, lvl) for m, lvl in row.items()}
            expected.discard(None)
            actual = set(PERSONA_ROLE_BUNDLES[persona])
            generated = {r for r in actual if r in MODULE_ROLE_PERMS}
            self.assertEqual(generated, expected, persona)

    def test_cashier_gets_no_salary_or_staff_role(self):
        cashier = set(PERSONA_ROLE_BUNDLES["Cashier"])
        self.assertNotIn("Barakat Salary Reader", cashier)
        self.assertNotIn("Barakat Salary Writer", cashier)
        self.assertNotIn("Barakat Staff Reader", cashier)
        self.assertNotIn("Barakat Staff Writer", cashier)

    def test_payroll_authoring_belongs_to_the_accountant(self):
        """Traded 2026-08-02, and checked both ways on purpose.

        The decision was to MOVE payroll authoring, so HR losing it and the Accountant
        gaining it are one fact — a swap applied to only one row is the failure worth
        catching. `Barakat Salary Writer` is what carries submit on Salary Slip, so it
        is the difference between issuing a payslip and saving a draft.
        """
        hr = set(PERSONA_ROLE_BUNDLES["HR"])
        accountant = set(PERSONA_ROLE_BUNDLES["Accountant"])
        self.assertIn("Barakat Salary Reader", hr)
        self.assertNotIn("Barakat Salary Writer", hr)
        self.assertIn("Barakat Salary Writer", accountant)
        self.assertNotIn("Barakat Salary Reader", accountant)

    def test_till_personas_keep_the_pos_operator_role(self):
        for persona in ("Manager", "Branch Supervisor"):
            self.assertIn("Barakat POS Operator", PERSONA_ROLE_BUNDLES[persona], persona)

    def test_every_bundle_role_is_provisioned(self):
        """A role no one mints is silently dropped by persona_role_bundle."""
        from barakat.permissions import ALL_ROLE_PERMS, EXTERNALLY_PERMED_ROLES

        known = set(ALL_ROLE_PERMS) | EXTERNALLY_PERMED_ROLES
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            for role in roles:
                self.assertIn(role, known, f"{persona} names unprovisioned {role}")

    def test_no_bundle_leaks_forbidden_role(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            self.assertEqual(FORBIDDEN_ROLES.intersection(roles), set(), persona)


class SelfServiceScope(unittest.TestCase):
    """The replacement for the native Employee / Employee Self Service roles."""

    def test_self_service_only_caller_is_scoped(self):
        self.assertTrue(self_scope_applies("Salary Slip", [SELF_SERVICE_ROLE]))
        self.assertTrue(self_scope_applies("Employee", [SELF_SERVICE_ROLE]))

    def test_hr_is_not_scoped(self):
        # THE trap: a permission_query_conditions hook applies to EVERY user of that
        # doctype. Scoping HR to its own record breaks payroll outright.
        hr = PERSONA_ROLE_BUNDLES["HR"]
        self.assertFalse(self_scope_applies("Salary Slip", hr))
        self.assertFalse(self_scope_applies("Employee", hr))

    def test_manager_is_not_scoped(self):
        manager = PERSONA_ROLE_BUNDLES["Manager"]
        self.assertFalse(self_scope_applies("Salary Slip", manager))
        self.assertFalse(self_scope_applies("Employee", manager))

    def test_cashier_is_scoped_on_both(self):
        cashier = PERSONA_ROLE_BUNDLES["Cashier"]
        self.assertTrue(self_scope_applies("Salary Slip", cashier))
        self.assertTrue(self_scope_applies("Employee", cashier))

    def test_accountant_reads_salary_unscoped_but_staff_scoped(self):
        # Accountant is salary: read (payroll reporting) but staff: none.
        accountant = PERSONA_ROLE_BUNDLES["Accountant"]
        self.assertFalse(self_scope_applies("Salary Slip", accountant))
        self.assertTrue(self_scope_applies("Employee", accountant))

    def test_caller_without_self_service_is_untouched(self):
        # An owner / System Manager holds neither role: the hook must stand down so
        # its query is exactly what it was before the hook existed.
        self.assertFalse(self_scope_applies("Employee", ["System Manager"]))
        self.assertFalse(self_scope_applies("Employee", []))

    def test_unrelated_doctype_never_scoped(self):
        self.assertFalse(self_scope_applies("Item", [SELF_SERVICE_ROLE]))

    def test_every_persona_holds_self_service(self):
        for persona, roles in PERSONA_ROLE_BUNDLES.items():
            self.assertIn(SELF_SERVICE_ROLE, roles, persona)

    def test_preserved_roles_no_longer_carry_the_leak(self):
        self.assertNotIn("Employee", PRESERVED_ROLES)
        self.assertNotIn("Employee Self Service", PRESERVED_ROLES)


if __name__ == "__main__":
    unittest.main()
