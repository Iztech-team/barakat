"""Unit tests for the "may this persona hold a POS PIN" decision.

The decision is pure and frappe-free on purpose — `permissions.py` imports nothing
from frappe — so every rule below is provable without a bench. The wiring that
applies it to an Employee save is tested separately, on a bench, in
`test_pos_pin_role_hook.py`, because permlevel behaviour cannot be faked.
"""

import unittest

from barakat.permissions import (
	PERSONAS,
	POS_PERSONAS,
	is_pos_persona,
	pin_role_decision,
)


class PosPersonaSet(unittest.TestCase):
	def test_exactly_the_three_till_personas(self):
		# Kept in step by hand with three other repos. If this fails, the other
		# three copies named in permissions.py need the same edit.
		self.assertEqual(
			POS_PERSONAS, frozenset({"Manager", "Branch Supervisor", "Cashier"})
		)

	def test_every_pos_persona_is_a_real_persona(self):
		self.assertTrue(POS_PERSONAS <= PERSONAS)

	def test_the_other_personas_are_not_pos(self):
		# Named explicitly rather than derived, so adding a persona to the catalog
		# fails this test and forces a deliberate decision about the till.
		self.assertEqual(
			PERSONAS - POS_PERSONAS, frozenset({"Accountant", "HR", "Inventory Keeper"})
		)


class IsPosPersona(unittest.TestCase):
	def test_the_three_are_true(self):
		for preset in ("Manager", "Branch Supervisor", "Cashier"):
			self.assertTrue(is_pos_persona(preset), preset)

	def test_non_pos_personas_are_false(self):
		for preset in ("HR", "Accountant", "Inventory Keeper"):
			self.assertFalse(is_pos_persona(preset), preset)

	def test_blank_and_missing_are_false(self):
		# An allow-list: absence of a role is not permission to use a till. 129
		# imported employees on bom sit in exactly this state.
		for preset in ("", "   ", None):
			self.assertFalse(is_pos_persona(preset), repr(preset))

	def test_an_unrecognised_preset_is_false(self):
		# Fails closed. A persona invented later is not a till operator until
		# somebody adds it to POS_PERSONAS here and in the other three repos.
		for preset in ("Kashier", "Owner", "System Manager", "Branch Manager"):
			self.assertFalse(is_pos_persona(preset), preset)

	def test_surrounding_whitespace_is_ignored(self):
		self.assertTrue(is_pos_persona("  Cashier  "))

	def test_case_must_match(self):
		# Presets are stored verbatim from a fixed radio list, never typed. Matching
		# loosely would let a hand-made "cashier" through a rule the AP cannot produce.
		self.assertFalse(is_pos_persona("cashier"))
		self.assertFalse(is_pos_persona("CASHIER"))


class PinRoleDecision(unittest.TestCase):
	def test_a_pos_persona_keeps_its_pin(self):
		self.assertEqual(pin_role_decision("Cashier", "1234", "1234"), "keep")
		self.assertEqual(pin_role_decision("Manager", "9999", "1234"), "keep")
		self.assertEqual(pin_role_decision("Branch Supervisor", "1234", None), "keep")

	def test_a_non_pos_persona_with_no_pin_is_left_alone(self):
		# The overwhelmingly common save: an HR clerk's salary or attendance edit.
		# It must not throw, and must not write.
		for stored in (None, "", "   "):
			self.assertEqual(pin_role_decision("HR", "", stored), "keep")
			self.assertEqual(pin_role_decision("HR", None, stored), "keep")

	def test_a_left_over_pin_on_a_non_pos_persona_is_cleared(self):
		# The ticket. The PIN is unchanged from what is on record, so the person
		# doing this is changing the ROLE, not typing a PIN. Say nothing, clear it.
		self.assertEqual(pin_role_decision("HR", "1234", "1234"), "clear")
		self.assertEqual(pin_role_decision("Accountant", "4471", "4471"), "clear")

	def test_whitespace_does_not_turn_a_left_over_pin_into_a_new_one(self):
		self.assertEqual(pin_role_decision("HR", " 1234 ", "1234"), "clear")

	def test_setting_a_pin_on_a_non_pos_persona_is_refused(self):
		# Reachable from the ERPNext desk and from a hand-made API call. Silently
		# discarding what somebody typed is its own bug.
		self.assertEqual(pin_role_decision("HR", "5678", "1234"), "refuse")
		self.assertEqual(pin_role_decision("Inventory Keeper", "5678", None), "refuse")
		self.assertEqual(pin_role_decision("Accountant", "5678", ""), "refuse")

	def test_a_blank_preset_is_treated_as_non_pos(self):
		self.assertEqual(pin_role_decision("", "1234", "1234"), "clear")
		self.assertEqual(pin_role_decision(None, "1234", "1234"), "clear")
		self.assertEqual(pin_role_decision("", "5678", "1234"), "refuse")

	def test_an_unknown_preset_is_treated_as_non_pos(self):
		self.assertEqual(pin_role_decision("Kashier", "1234", "1234"), "clear")
		self.assertEqual(pin_role_decision("Kashier", "5678", "1234"), "refuse")

	def test_a_system_context_never_refuses(self):
		# A migration, a patch or an install must never die on this rule. Whatever
		# it is carrying gets cleared instead of throwing — the same stand-down
		# guard_role_preset makes for the same reason.
		self.assertEqual(
			pin_role_decision("HR", "5678", "1234", system_context=True), "clear"
		)
		self.assertEqual(
			pin_role_decision("Cashier", "5678", "1234", system_context=True), "keep"
		)


if __name__ == "__main__":
	unittest.main()
