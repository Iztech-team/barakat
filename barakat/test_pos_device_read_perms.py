"""Pure, Frappe-free tests: every doctype a POS till pulls at sync time must be
readable by a persona that can actually log the till in.

Runs locally:  python -m unittest barakat.test_pos_device_read_perms

Only `Manager` and `Branch Supervisor` may establish a POS device session
(barakat.api.session_role.get_my_pos_role). The desktop app wraps each sync pull
in a try/catch and falls back to defaults, so a missing read permission does not
crash anything — it silently degrades the till. These tests are the guard: they
fail loudly if a bundle stops covering one of the reads.
"""

import unittest

from barakat.permissions import BARAKAT_ROLE_PERMS, bundle_for

# The persona bundles that may log a POS device in.
POS_LOGIN_PERSONAS = ("Manager", "Branch Supervisor")

# Doctypes the till reads that ship with `System Manager`-only DocPerms, i.e. the
# ones a persona can ONLY reach through a `Barakat *` role in this file. Native
# ERPNext doctypes (Item, Customer, Bin, ...) are covered by the native roles in
# each bundle and are not re-asserted here.
POS_DEVICE_READS = (
    "POS Scale Settings",  # scale-barcode rules + has_balances, per branch
    "Device",  # this till's paired device record
    "System Settings",  # rounding_method fallback
    "Global Defaults",  # disable_rounded_total + default_currency fallback
)

# Roles outside this file that also carry the read. `Barakat Settings Manager`
# gets System Settings / Global Defaults read from
# barakat.setup.install._grant_settings_manager_perms, not from BARAKAT_ROLE_PERMS.
EXTERNAL_READ_GRANTS = {
    "Barakat Settings Manager": ("System Settings", "Global Defaults"),
}


def _readable_doctypes(role):
    perms = BARAKAT_ROLE_PERMS.get(role, {})
    from_file = {dt for dt, p in perms.items() if "read" in p}
    return from_file | set(EXTERNAL_READ_GRANTS.get(role, ()))


class PosLoginPersonasCanReadDeviceConfig(unittest.TestCase):
    def test_every_pos_login_persona_covers_every_device_read(self):
        for persona in POS_LOGIN_PERSONAS:
            readable = set()
            for role in bundle_for(persona):
                readable |= _readable_doctypes(role)
            for doctype in POS_DEVICE_READS:
                with self.subTest(persona=persona, doctype=doctype):
                    self.assertIn(
                        doctype,
                        readable,
                        f"{persona} cannot read {doctype!r} — its till will silently "
                        f"fall back to defaults instead of the shop's configuration",
                    )


class PosOperatorStaysReadOnlyOnSettings(unittest.TestCase):
    """The till reads the rounding singles; it must never be able to change them.

    Writes go through barakat.api.settings.set_rounding_settings, which checks
    ROUNDING_WRITER_ROLES — a set this role is deliberately absent from.
    """

    def test_no_write_on_the_global_singles(self):
        perms = BARAKAT_ROLE_PERMS["Barakat POS Operator"]
        for doctype in ("System Settings", "Global Defaults"):
            with self.subTest(doctype=doctype):
                self.assertEqual(perms[doctype], ("read",))

    def test_scale_settings_and_device_are_read_only(self):
        perms = BARAKAT_ROLE_PERMS["Barakat POS Operator"]
        for doctype in ("POS Scale Settings", "Device"):
            with self.subTest(doctype=doctype):
                self.assertEqual(perms[doctype], ("read",))


class NonPosPersonasDoNotGetTheDeviceReads(unittest.TestCase):
    """The grant rides on `Barakat POS Operator`, which only the two POS-login
    personas hold. Cashier/Accountant/Inventory Keeper/HR must not pick it up."""

    def test_pos_operator_is_limited_to_the_pos_login_personas(self):
        holders = {
            persona
            for persona in ("Manager", "Branch Supervisor", "Cashier", "Accountant",
                            "Inventory Keeper", "HR")
            if "Barakat POS Operator" in bundle_for(persona)
        }
        self.assertEqual(holders, set(POS_LOGIN_PERSONAS))


if __name__ == "__main__":
    unittest.main()
