"""On-bench tests for barakat.api.shift.get_shift_invoices.

Run on a site:
    bench --site <site> run-tests --module barakat.api.test_shift_invoices
Not runnable on the Windows dev box (imports `frappe`).

ERPNext's own get_invoices filters POS Invoices by `owner`, which silently drops
the invoices of a second account that sold on the same shift — they are never
consolidated. These tests assert the two rules that replace it: scope by the
opening entry's OWN period, and never by owner.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.api import shift as shift_api

OPENING = frappe._dict(
	name="POS-OPE-0001",
	pos_profile="Main",
	period_start_date="2026-07-26 13:47:44",
)

ROWS = [
	frappe._dict(
		name="ACC-PSINV-0001",
		customer="A",
		posting_date="2026-07-26",
		posting_time="21:29:38",
		grand_total=10.0,
		net_total=10.0,
		total_qty=1.0,
		total_taxes_and_charges=0.0,
		is_return=0,
		return_against=None,
		invoice_type="POS Invoice",
	),
	frappe._dict(
		name="ACC-PSINV-0002",
		customer="B",
		posting_date="2026-07-26",
		posting_time="23:34:40",
		grand_total=20.0,
		net_total=20.0,
		total_qty=1.0,
		total_taxes_and_charges=0.0,
		is_return=0,
		return_against=None,
		invoice_type="POS Invoice",
	),
]


class SelectionRules(FrappeTestCase):
	def _call(self):
		self.captured = {}

		def fake_sql(query, values=None, as_dict=False):
			self.captured["query"] = query
			self.captured["values"] = values
			return ROWS

		with patch("frappe.db.get_value", return_value=OPENING), patch(
			"frappe.db.sql", side_effect=fake_sql
		), patch("barakat.api.shift.get_payments", return_value=[]), patch(
			"barakat.api.shift.get_taxes", return_value=[]
		):
			return shift_api.get_shift_invoices("POS-OPE-0001")

	def test_returns_every_invoice_regardless_of_owner(self):
		result = self._call()
		self.assertEqual(
			[i["name"] for i in result["invoices"]],
			["ACC-PSINV-0001", "ACC-PSINV-0002"],
		)

	def test_never_filters_by_owner(self):
		self._call()
		self.assertNotIn("owner", self.captured["query"].lower())

	def test_window_starts_at_the_openings_own_period_start(self):
		self._call()
		self.assertEqual(self.captured["values"]["start"], "2026-07-26 13:47:44")

	def test_scoped_to_the_openings_pos_profile(self):
		self._call()
		self.assertEqual(self.captured["values"]["profile"], "Main")

	def test_excludes_consolidated_and_unsubmitted_invoices(self):
		self._call()
		query = self.captured["query"]
		self.assertIn("consolidated_invoice", query)
		self.assertIn("docstatus = 1", query)


class MissingOpening(FrappeTestCase):
	def test_throws_when_the_opening_entry_does_not_exist(self):
		with patch("frappe.db.get_value", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				shift_api.get_shift_invoices("POS-OPE-NOPE")


if __name__ == "__main__":
	unittest.main()
