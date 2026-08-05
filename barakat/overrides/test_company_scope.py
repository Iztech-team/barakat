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

from barakat.overrides.company_marker import COMPANY_MARKER_FIELDS
from barakat.overrides.company_scope import (
	COMPANY_NEUTRAL_DOCTYPES,
	GUARDED_DOCTYPES,
	company_field_for,
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
					for attribute in ("fieldtype", "options", "insert_after", "label"):
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

	def test_a_shop_owned_doctype_with_no_company_column_is_refused(self):
		"""The point of the whole thing: an unclassified doctype shows nothing."""
		self.assertTrue(strict_scope_enabled(), "kill switch is off on this site")
		with unittest.mock.patch(
			"barakat.overrides.company_scope._caller_is_tenant_scoped", return_value=True
		):
			# `Note` is a real doctype with no company field, and is not classified.
			self.assertEqual(unscopable_block("someone@example.com", "Note"), "1=0")

	def test_rail_1_a_doctype_we_ship_a_marker_for_is_never_blocked(self):
		"""Mid-deploy the column is briefly absent; blocking then empties live forms."""
		for doctype in COMPANY_MARKER_FIELDS:
			with self.subTest(doctype=doctype):
				with unittest.mock.patch(
					"barakat.overrides.company_scope.company_field_for", return_value=None
				), unittest.mock.patch(
					"barakat.overrides.company_scope._caller_is_tenant_scoped",
					return_value=True,
				):
					self.assertEqual(unscopable_block("someone@example.com", doctype), "")

	def test_rail_3_an_unreadable_meta_never_blacks_out(self):
		"""A transient failure must not be mistaken for a missing column."""
		with unittest.mock.patch(
			"barakat.overrides.company_scope.frappe.get_meta", side_effect=Exception("boom")
		):
			self.assertEqual(unscopable_block("someone@example.com", "Note"), "")

	def test_rail_4_a_caller_outside_the_tenant_boundary_is_untouched(self):
		"""Service accounts and the gateway hold no Company permission and must pass."""
		with unittest.mock.patch(
			"barakat.overrides.company_scope._caller_is_tenant_scoped", return_value=False
		):
			self.assertEqual(unscopable_block("service@example.com", "Note"), "")

	def test_the_kill_switch_disables_it_entirely(self):
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
