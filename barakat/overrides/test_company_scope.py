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

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.overrides.company_marker import COMPANY_MARKER_FIELDS
from barakat.overrides.company_scope import (
	COMPANY_NEUTRAL_DOCTYPES,
	GUARDED_DOCTYPES,
	company_field_for,
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
