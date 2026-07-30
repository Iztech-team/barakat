"""The `User` visibility tiers, as a pure decision — no bench required.

Guards the rule measured on bom.iztech.net (prod, 2026-07-30): a Cashier listed 26 of
28 users, and `bom3manager@gmail.com` — Manager of BOM3 on an EIGHT-company site — saw
21 users belonging to BOM. Two separate defects in one symptom: no self-scoping for
staff-less personas, and no shop boundary for the ones allowed a staff list.
"""

import unittest

from barakat.permissions import PERSONA_ROLE_BUNDLES, user_scope_for


class UserScopeTiers(unittest.TestCase):
	def test_personas_without_staff_access_see_only_themselves(self):
		# They never need a colleague list: the documents they read already carry the
		# names they display (a payslip carries employee_name).
		for persona in ("Cashier", "Accountant", "Inventory Keeper"):
			self.assertEqual(
				user_scope_for(PERSONA_ROLE_BUNDLES[persona]),
				"self",
				f"{persona} must not be able to enumerate colleagues",
			)

	def test_staff_facing_personas_are_scoped_to_their_own_shop(self):
		for persona in ("Manager", "HR", "Branch Supervisor"):
			self.assertEqual(
				user_scope_for(PERSONA_ROLE_BUNDLES[persona]),
				"company",
				f"{persona} needs a staff list, but only its own shop's",
			)

	def test_no_persona_is_unrestricted(self):
		# `unrestricted` is the owner tier. A persona reaching it would restore the
		# cross-shop leak in full.
		for persona, bundle in PERSONA_ROLE_BUNDLES.items():
			self.assertNotEqual(
				user_scope_for(bundle), "unrestricted", f"{persona} must not be unrestricted"
			)

	def test_owner_and_administrator_stand_down(self):
		# A permission_query_conditions hook applies to EVERY caller, so this is not an
		# optimisation — narrowing the owner would lock them out of their own site.
		self.assertEqual(user_scope_for(["System Manager"]), "unrestricted")
		self.assertEqual(user_scope_for(["Administrator"]), "unrestricted")

	def test_an_unknown_role_set_fails_closed(self):
		# A retired or mistyped role must land on the narrowest tier, not the widest.
		self.assertEqual(user_scope_for([]), "self")
		self.assertEqual(user_scope_for(["Not A Real Role"]), "self")

	def test_holding_a_staff_role_alongside_a_narrow_one_still_scopes_by_company(self):
		# Tier is decided by the WIDEST need, so a Cashier who is also given
		# attendance duties gets a staff list — but still only its own shop's.
		mixed = list(PERSONA_ROLE_BUNDLES["Cashier"]) + ["Barakat Attendance Manager"]
		self.assertEqual(user_scope_for(mixed), "company")


if __name__ == "__main__":
	unittest.main()
