"""Pure, Frappe-free tests for "whose ERPNext session must this save end".

QA 0001-607: a permission change was not enforced until the next full sign-in. The
proxy half of that fix re-reads the persona when a token is refreshed; this half is
what makes the change bite immediately and durably — ERPNext drops the staff
member's own session the moment their access is taken away, for every process and
across a restart, which no in-memory list in the proxy can do.

Runs locally:  python -m unittest barakat.test_session_signout
"""

import unittest

from barakat.permissions import login_to_sign_out


def _call(**overrides):
    """The decision's inputs, defaulting to an ordinary no-op edit."""
    kwargs = {
        "previous_login": "keeper@example.com",
        "current_login": "keeper@example.com",
        "previous_preset": "Inventory Keeper",
        "current_preset": "Inventory Keeper",
        "previous_status": "Active",
        "current_status": "Active",
    }
    kwargs.update(overrides)
    return login_to_sign_out(**kwargs)


class AccessTakenAway(unittest.TestCase):
    def test_demotion_signs_the_old_login_out(self):
        self.assertEqual(_call(current_preset="Cashier"), "keeper@example.com")

    def test_promotion_signs_them_out_too(self):
        # Not a security case, but the same staleness: their token would keep the
        # persona they have just left until they sign in again either way.
        self.assertEqual(_call(current_preset="Manager"), "keeper@example.com")

    def test_offboarding_signs_them_out(self):
        self.assertEqual(_call(current_status="Left"), "keeper@example.com")

    def test_suspension_signs_them_out(self):
        self.assertEqual(_call(current_status="Suspended"), "keeper@example.com")

    def test_moving_the_login_signs_the_OLD_address_out(self):
        # The address in the token they are holding is the one that had the session.
        self.assertEqual(
            _call(current_login="new.address@example.com"), "keeper@example.com"
        )

    def test_unlinking_the_login_signs_it_out(self):
        self.assertEqual(_call(current_login=""), "keeper@example.com")

    def test_losing_the_persona_entirely_signs_them_out(self):
        self.assertEqual(_call(current_preset=""), "keeper@example.com")


class NothingTakenAway(unittest.TestCase):
    def test_an_unrelated_edit_leaves_them_signed_in(self):
        # A corrected birthday or a salary change must not throw somebody off a till.
        self.assertEqual(_call(), "")

    def test_a_rehire_leaves_them_signed_in(self):
        self.assertEqual(_call(previous_status="Left", current_status="Active"), "")

    def test_giving_an_employee_their_first_login_is_not_a_removal(self):
        self.assertEqual(
            _call(previous_login="", current_login="fresh@example.com"), ""
        )

    def test_an_insert_has_no_previous_state_to_lose(self):
        self.assertEqual(
            _call(
                previous_login="",
                previous_preset="",
                previous_status="",
                current_preset="Cashier",
            ),
            "",
        )

    def test_an_employee_with_no_login_is_a_no_op(self):
        self.assertEqual(
            _call(previous_login="", current_login="", current_preset="Cashier"), ""
        )


class Normalisation(unittest.TestCase):
    def test_none_and_blank_are_the_same_absence(self):
        # An employee who had no persona and still has none has lost nothing.
        self.assertEqual(_call(previous_preset=None, current_preset=""), "")

    def test_a_status_that_cannot_be_read_counts_as_not_working(self):
        # Fail safe: "Active -> unknown" is treated as access removed, not kept.
        self.assertEqual(_call(current_status=None), "keeper@example.com")

    def test_whitespace_is_not_a_change(self):
        self.assertEqual(_call(current_preset="  Inventory Keeper  "), "")

    def test_the_returned_login_is_stripped(self):
        self.assertEqual(
            _call(previous_login="  keeper@example.com  ", current_preset="Cashier"),
            "keeper@example.com",
        )


if __name__ == "__main__":
    unittest.main()
