"""Every guarded doctype must be scopable, or explicitly declared site-wide.

This is the test that would have caught the 2026-08-05 leak. `GUARDED_DOCTYPES` is
generated from `MODULE_DOCTYPES`, so registration was never the problem — Contact and
Item Price were both registered. What was missing was any assertion that a registered
doctype can actually BE scoped, and both of them silently fell through to an unfiltered
query for every persona on an 8-company production site.

Keep this test cheap and keep it honest: it reads real metadata off the site, so it also
catches a marker that was removed, renamed, or downgraded from Link to Data.
"""

import json
import pathlib
import unittest.mock

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.company_marker import (
	COMPANY_MARKER_FIELDS,
	MANDATORY_MARKER_DOCTYPES,
)
from barakat.overrides.company_scope import (
	COMPANY_NEUTRAL_DOCTYPES,
	GUARDED_DOCTYPES,
	blank_marker_block,
	company_field_for,
	get_permission_query_conditions,
	has_permission,
	strict_scope_enabled,
	unscopable_block,
)

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "custom_field.json"


class TestCompanyScopeCoverage(FrappeTestCase):
	def test_every_guarded_doctype_is_scopable_or_declared_neutral(self):
		"""No guarded doctype may be silently unscopable.

		A failure here is a decision to make, not a line to delete: either give the
		doctype a `custom_company` marker (see `company_marker.py`, and remember the
		backfill — a blank marker is visible to everyone), or add it to
		COMPANY_NEUTRAL_DOCTYPES with a comment saying why a leak of it is acceptable.
		"""
		unscopable = sorted(
			doctype
			for doctype in GUARDED_DOCTYPES
			if not company_field_for(doctype) and doctype not in COMPANY_NEUTRAL_DOCTYPES
		)
		self.assertEqual(
			unscopable,
			[],
			f"guarded but unscopable: {unscopable}. Give each a company marker or "
			f"declare it in COMPANY_NEUTRAL_DOCTYPES.",
		)

	def test_the_repaired_doctypes_carry_their_marker(self):
		"""Pin the specific regression. These leaked; they must stay scopable."""
		for doctype in (
			"Contact",
			"Item Price",
			"Product Bundle",
			"Supplier Group",
			"Territory",
		):
			with self.subTest(doctype=doctype):
				self.assertEqual(
					company_field_for(doctype),
					"custom_company",
					f"{doctype} lost its company marker - this is the 2026-08-05 leak",
				)

	def test_neutral_list_holds_no_doctype_that_grew_a_marker(self):
		"""A neutral entry that now has a marker is a stale exemption, not a rule.

		Left in place it would keep a real boundary switched off: `has_permission` and
		the query condition both treat a neutral doctype as not shop-owned.
		"""
		stale = sorted(
			doctype for doctype in COMPANY_NEUTRAL_DOCTYPES if company_field_for(doctype)
		)
		self.assertEqual(
			stale, [], f"declared company-neutral but now scopable: {stale}"
		)

	def test_neutral_list_holds_no_doctype_nobody_guards(self):
		"""An exemption for a doctype outside the matrix is dead weight and misleads."""
		unknown = sorted(set(COMPANY_NEUTRAL_DOCTYPES) - set(GUARDED_DOCTYPES))
		self.assertEqual(unknown, [], f"not a guarded doctype: {unknown}")

	def test_marker_definitions_match_the_fixtures(self):
		"""The patch and the fixtures must ship the same field.

		The backfill patch creates these itself, because it runs before `sync_fixtures`
		and on an upgrading site the column does not exist yet. So the definition lives
		in two places; if they drift, a fresh install and an upgraded one end up with
		different fields and only one of them is scoped.
		"""
		shipped = {
			row.get("name"): row for row in json.loads(FIXTURE.read_text(encoding="utf-8"))
		}
		for doctype, fields in COMPANY_MARKER_FIELDS.items():
			for field in fields:
				key = f"{doctype}-{field['fieldname']}"
				with self.subTest(field=key):
					self.assertIn(key, shipped, f"{key} is not in the fixtures")
					for attribute in (
						"fieldtype",
						"options",
						"insert_after",
						"label",
						# `reqd` is the 0001-606 half. Drifting here would ship a
						# fresh install whose rows can be blank while an upgraded
						# site's cannot, which is the worst of both.
						"reqd",
					):
						self.assertEqual(
							shipped[key].get(attribute), field.get(attribute), attribute
						)

	def test_no_guarded_doctype_is_blocked_today(self):
		"""The blackout must be inert on a healthy site.

		If this fails, real users are seeing an empty list RIGHT NOW. It is the test
		to run first when someone reports "the list went blank after the update".
		"""
		blocked = sorted(
			doctype
			for doctype in GUARDED_DOCTYPES
			if unscopable_block("test-tenant-user@example.com", doctype)
		)
		self.assertEqual(blocked, [], f"these are being blacked out: {blocked}")

	def test_it_is_off_unless_a_site_opts_in(self):
		"""Default OFF. A site arms it deliberately; it is never inherited."""
		self.assertFalse(strict_scope_enabled())
		with unittest.mock.patch(
			"barakat.overrides.company_scope._caller_is_tenant_scoped", return_value=True
		):
			self.assertEqual(unscopable_block("someone@example.com", "Note"), "")

	def test_a_shop_owned_doctype_with_no_company_column_is_refused_when_armed(self):
		"""The point of the whole thing: an unclassified doctype shows nothing."""
		with unittest.mock.patch.dict(
			frappe.conf, {"barakat_strict_company_scope": 1}
		), unittest.mock.patch(
			"barakat.overrides.company_scope._caller_is_tenant_scoped", return_value=True
		):
			self.assertTrue(strict_scope_enabled())
			# `Note` is a real doctype with no company field, and is not classified.
			self.assertEqual(unscopable_block("someone@example.com", "Note"), "1=0")

	# The three rails below are only meaningful with the switch ARMED. Without
	# `_armed()` the flag short-circuits first and every one of them passes for the
	# wrong reason — a green test that proves nothing.
	def _armed(self):
		return unittest.mock.patch.dict(frappe.conf, {"barakat_strict_company_scope": 1})

	def test_rail_1_a_doctype_we_ship_a_marker_for_is_never_blocked(self):
		"""Mid-deploy the column is briefly absent; blocking then empties live forms."""
		for doctype in COMPANY_MARKER_FIELDS:
			with self.subTest(doctype=doctype):
				with self._armed(), unittest.mock.patch(
					"barakat.overrides.company_scope.company_field_for", return_value=None
				), unittest.mock.patch(
					"barakat.overrides.company_scope._caller_is_tenant_scoped",
					return_value=True,
				):
					self.assertEqual(unscopable_block("someone@example.com", doctype), "")

	def test_rail_3_an_unreadable_meta_never_blacks_out(self):
		"""A transient failure must not be mistaken for a missing column."""
		with self._armed(), unittest.mock.patch(
			"barakat.overrides.company_scope.frappe.get_meta", side_effect=Exception("boom")
		), unittest.mock.patch(
			"barakat.overrides.company_scope._caller_is_tenant_scoped", return_value=True
		):
			self.assertEqual(unscopable_block("someone@example.com", "Note"), "")

	def test_rail_4_a_caller_outside_the_tenant_boundary_is_untouched(self):
		"""Service accounts and the gateway hold no Company permission and must pass."""
		with self._armed(), unittest.mock.patch(
			"barakat.overrides.company_scope._caller_is_tenant_scoped", return_value=False
		):
			self.assertEqual(unscopable_block("service@example.com", "Note"), "")

	def test_disarming_it_again_stops_the_blackout(self):
		with unittest.mock.patch.dict(frappe.conf, {"barakat_strict_company_scope": 0}):
			self.assertFalse(strict_scope_enabled())
			self.assertEqual(unscopable_block("someone@example.com", "Note"), "")

	def test_markers_are_link_fields_pointing_at_company(self):
		"""A Data field named `company` holds a name, not a link, and cannot be pinned."""
		for doctype in GUARDED_DOCTYPES:
			field = company_field_for(doctype)
			if not field or field == "name":
				# `Company` is pinned by its own name, which is not a field at all.
				continue
			with self.subTest(doctype=doctype):
				meta_field = frappe.get_meta(doctype).get_field(field)
				self.assertIsNotNone(meta_field, f"{doctype}.{field} does not exist")
				self.assertEqual(meta_field.fieldtype, "Link")
				self.assertEqual(meta_field.options, "Company")


class TestMandatoryCompanyMarker(FrappeTestCase):
	"""A row of these three may not exist without an owner.

	The 2026-08-05 fix made the doctypes scopable; it left rows that nothing could
	derive an owner for, and a blank marker is readable by every shop. Ticket 0001-606
	re-measured exactly that. `reqd` removes the case, the same way the presence
	doctypes have always done it (`presence/test_doctypes.py`).
	"""

	def test_the_marker_is_mandatory_on_every_row_owning_doctype(self):
		for doctype in MANDATORY_MARKER_DOCTYPES:
			with self.subTest(doctype=doctype):
				field = frappe.get_meta(doctype).get_field("custom_company")
				self.assertIsNotNone(field, f"{doctype} has no custom_company field")
				self.assertTrue(
					field.reqd,
					f"{doctype}.custom_company must be reqd - a blank marker is "
					f"visible to every shop on the site",
				)

	def test_the_shared_tree_masters_are_never_made_mandatory(self):
		"""Excluded on purpose, and a fresh install depends on it.

		Every Supplier Group and Territory on every production site is an ERPNext seed
		inserted by Administrator, for whom no company can be derived. Mandatory there
		would fail `bench new-site`, not close a leak.
		"""
		for doctype in ("Supplier Group", "Territory"):
			with self.subTest(doctype=doctype):
				self.assertNotIn(doctype, MANDATORY_MARKER_DOCTYPES)
				field = frappe.get_meta(doctype).get_field("custom_company")
				self.assertFalse(
					field.reqd,
					f"{doctype}.custom_company must stay optional - its rows are "
					f"shared ERPNext seeds",
				)

	def test_every_mandatory_doctype_ships_a_marker(self):
		"""The two lists must not drift: a name here with no field is a no-op."""
		for doctype in MANDATORY_MARKER_DOCTYPES:
			self.assertIn(doctype, COMPANY_MARKER_FIELDS)


class TestBlankMarkerBlock(FrappeTestCase):
	"""The read-time half: a legacy blank row is refused, not shared.

	`reqd` stops new blanks. It cannot reach the rows already in the table, and on a
	multi-company site those are the leak the ticket measured.
	"""

	def _multi(self, value=True):
		return unittest.mock.patch(
			"barakat.overrides.company_scope.multi_company_site", return_value=value
		)

	def _tenant(self, value=True):
		return unittest.mock.patch(
			"barakat.overrides.company_scope._caller_is_tenant_scoped", return_value=value
		)

	def test_it_refuses_a_blank_row_on_a_multi_company_site(self):
		with self._multi(), self._tenant():
			self.assertEqual(
				blank_marker_block("someone@example.com", "Item Price"),
				"ifnull(`tabItem Price`.`custom_company`, '') != ''",
			)

	def test_a_single_company_site_is_untouched(self):
		"""Every Barakat shop site today. A blank there leaks to nobody, and hiding
		it would empty a live list for no gain."""
		with self._multi(False), self._tenant():
			for doctype in MANDATORY_MARKER_DOCTYPES:
				with self.subTest(doctype=doctype):
					self.assertEqual(blank_marker_block("someone@example.com", doctype), "")

	def test_a_caller_outside_the_tenant_boundary_is_untouched(self):
		"""The gateway's SSO user and background jobs hold no Company permission."""
		with self._multi(), self._tenant(False):
			self.assertEqual(blank_marker_block("service@example.com", "Contact"), "")

	def test_it_never_touches_a_doctype_whose_blank_is_shared(self):
		"""`All Item Groups`, the stock `Cash` mode, `Standard Selling`, global UOMs.

		Hiding those breaks the shop instead of protecting it - `treeview.py` keeps
		blanks visible for the same reason.
		"""
		for doctype in ("Item Group", "Mode of Payment", "Price List", "UOM", "Territory"):
			with self.subTest(doctype=doctype):
				with self._multi(), self._tenant():
					self.assertEqual(blank_marker_block("someone@example.com", doctype), "")

	def test_a_missing_column_mid_deploy_never_blacks_out(self):
		"""Patches run before `sync_fixtures`; the column is briefly absent."""
		with self._multi(), self._tenant(), unittest.mock.patch(
			"barakat.overrides.company_scope.company_field_for", return_value=None
		):
			self.assertEqual(blank_marker_block("someone@example.com", "Contact"), "")

	def test_the_list_query_carries_the_condition(self):
		"""It must reach the SQL, not just the helper."""
		with self._multi(), self._tenant():
			condition = get_permission_query_conditions(
				user="someone@example.com", doctype="Contact"
			)
			self.assertIn("ifnull(`tabContact`.`custom_company`, '') != ''", condition)

	def test_opening_a_blank_row_by_name_is_refused_too(self):
		"""A row hidden from the list but openable by name is not a boundary."""
		with self._multi(), self._tenant():
			self.assertFalse(
				has_permission(
					{"doctype": "Contact", "custom_company": ""}, user="someone@example.com"
				)
			)

	def test_a_stamped_row_is_still_readable(self):
		"""The guard must only cost blank rows. This is the no-blackout rail."""
		company = frappe.get_all("Company", pluck="name", limit=1)[0]
		with self._multi(), self._tenant():
			self.assertTrue(
				has_permission(
					{"doctype": "Contact", "custom_company": company},
					user="someone@example.com",
				)
			)


class TestMarkerStampingOnSave(FrappeTestCase):
	"""Mandatory is only safe if the stamp always gets there first.

	Frappe runs `validate` hooks in `run_before_save_methods()` and the mandatory
	check in `_validate()` on the next line, so these prove the ordering as much as
	the derivation. A regression here does not leak — it stops shops saving.
	"""

	def setUp(self):
		self.company = frappe.get_all("Company", pluck="name", limit=1)[0]

	def test_an_item_price_inherits_its_item_s_company(self):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Barakat 606 Item",
				"item_name": "_Barakat 606 Item",
				"item_group": frappe.get_all("Item Group", pluck="name", limit=1)[0],
				"custom_company": self.company,
				"is_stock_item": 0,
			}
		)
		item.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Item", item.name, force=True)

		price = frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": item.name,
				"price_list": frappe.get_all("Price List", pluck="name", limit=1)[0],
				"price_list_rate": 1,
			}
		)
		price.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Item Price", price.name, force=True)

		self.assertEqual(price.custom_company, self.company)

	def test_a_contact_inherits_the_party_it_is_linked_to(self):
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "_Barakat 606 Customer",
				"custom_company": self.company,
			}
		)
		customer.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Customer", customer.name, force=True)

		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "_Barakat 606 Contact",
				"links": [{"link_doctype": "Customer", "link_name": customer.name}],
			}
		)
		contact.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Contact", contact.name, force=True)

		self.assertEqual(contact.custom_company, self.company)

	def test_a_row_whose_owner_cannot_be_derived_is_refused_not_left_blank(self):
		"""The whole point. Before this, the same insert produced a blank row that
		every shop on the site could read."""
		with unittest.mock.patch(
			"barakat.overrides.company_marker.contact_company", return_value=""
		):
			contact = frappe.get_doc(
				{"doctype": "Contact", "first_name": "_Barakat 606 Orphan"}
			)
			with self.assertRaises(frappe.MandatoryError):
				contact.insert(ignore_permissions=True)
