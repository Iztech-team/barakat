"""Which tree rows a company-restricted caller may see — a pure decision, no bench.

Guards the leak measured on bom.iztech.net (prod, 2026-07-30). `cashierbom2@gmail.com`,
a Cashier of BOM2 on an EIGHT-company site:

    get_list("Item Group")  ->  7 rows, all company-less              (correct)
    tree get_children       -> 12 rows, six of them other shops':
                                 Audit Group - BOM4, Bakery - BOM,
                                 Drinks - BOM, Snacks - BOM,
                                 Products - BOM, osama%%% - BOM

Frappe's tree endpoint queries the table directly — no `has_permission`, no match
conditions — so the tree ignored a restriction the list view applied correctly.
"""

import unittest

from barakat.overrides.treeview import visible_rows


def row(name, expandable=0):
	return {"value": name, "title": name, "expandable": expandable}


# The exact tree the BOM2 cashier was served.
BOM_TREE = [
	row("Audit Group - BOM4"),
	row("Bakery - BOM"),
	row("Consumable"),
	row("Drinks - BOM"),
	row("Miscellaneous"),
	row("osama%%% - BOM"),
	row("Products"),
	row("Products - BOM", expandable=1),
	row("Raw Material"),
	row("Services"),
	row("Snacks - BOM"),
	row("Sub Assemblies"),
]

# custom_company as it really is on that site: the six shop groups are stamped, the
# six ERPNext defaults are not.
BOM_OWNERS = {
	"Audit Group - BOM4": "BOM4",
	"Bakery - BOM": "BOM",
	"Drinks - BOM": "BOM",
	"osama%%% - BOM": "BOM",
	"Products - BOM": "BOM",
	"Snacks - BOM": "BOM",
	"Consumable": None,
	"Miscellaneous": None,
	"Products": None,
	"Raw Material": None,
	"Services": None,
	"Sub Assemblies": None,
}


class TreeCompanyScoping(unittest.TestCase):
	def test_the_bom2_cashier_no_longer_sees_another_shops_groups(self):
		names = [r["value"] for r in visible_rows(BOM_TREE, BOM_OWNERS, {"BOM2"})]
		for leaked in (
			"Audit Group - BOM4",
			"Bakery - BOM",
			"Drinks - BOM",
			"osama%%% - BOM",
			"Products - BOM",
			"Snacks - BOM",
		):
			self.assertNotIn(leaked, names, f"{leaked} belongs to another shop")

	def test_the_shared_erpnext_defaults_survive(self):
		# `All Item Groups` and friends carry no company. They are the tree root and
		# the "no group" sentinel products are written against — hiding them empties
		# the tree and breaks item creation.
		names = [r["value"] for r in visible_rows(BOM_TREE, BOM_OWNERS, {"BOM2"})]
		self.assertEqual(
			names,
			["Consumable", "Miscellaneous", "Products", "Raw Material", "Services", "Sub Assemblies"],
		)

	def test_a_bom_cashier_sees_bom_groups_and_not_bom4s(self):
		names = [r["value"] for r in visible_rows(BOM_TREE, BOM_OWNERS, {"BOM"})]
		self.assertIn("Bakery - BOM", names)
		self.assertIn("Snacks - BOM", names)
		self.assertNotIn("Audit Group - BOM4", names)

	def test_a_caller_permitted_two_companies_sees_both(self):
		names = [r["value"] for r in visible_rows(BOM_TREE, BOM_OWNERS, {"BOM", "BOM4"})]
		self.assertIn("Bakery - BOM", names)
		self.assertIn("Audit Group - BOM4", names)

	def test_unrestricted_callers_are_untouched(self):
		# The owner and the Administrator hold no Company user permission. This hook
		# must never narrow what they already see.
		self.assertEqual(visible_rows(BOM_TREE, BOM_OWNERS, None), BOM_TREE)

	def test_an_empty_string_company_counts_as_shared(self):
		# Frappe writes "" as often as NULL for an unset Link.
		rows = [row("Blank"), row("Owned")]
		names = [r["value"] for r in visible_rows(rows, {"Blank": "", "Owned": "BOM4"}, {"BOM2"})]
		self.assertEqual(names, ["Blank"])

	def test_a_row_missing_from_the_owner_map_is_kept(self):
		# The company lookup returning nothing for a name must not silently delete it;
		# failing that way would empty trees on any doctype whose field we misread.
		names = [r["value"] for r in visible_rows([row("Mystery")], {}, {"BOM2"})]
		self.assertEqual(names, ["Mystery"])

	def test_an_expandable_row_is_filtered_like_any_other(self):
		# "Products - BOM" is a group node. Keeping parents "so the tree looks right"
		# would leave another shop's branch visible at the top level.
		names = [r["value"] for r in visible_rows([row("Products - BOM", expandable=1)], BOM_OWNERS, {"BOM2"})]
		self.assertEqual(names, [])

	def test_no_allowed_company_hides_every_owned_row(self):
		# An empty set is NOT the same as None: it means "restricted, and nothing
		# matches". Only the shared rows may survive.
		names = [r["value"] for r in visible_rows(BOM_TREE, BOM_OWNERS, set())]
		self.assertNotIn("Bakery - BOM", names)
		self.assertIn("Consumable", names)

	def test_an_empty_tree_stays_empty(self):
		self.assertEqual(visible_rows([], {}, {"BOM2"}), [])
