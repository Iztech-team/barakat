"""On-bench tests for the POS Closing Entry override.

Run on a site:
    bench --site <site> run-tests --module barakat.overrides.test_pos_closing_entry
Not runnable on the Windows dev box (imports `frappe`).

ERPNext refuses to consolidate a POS Invoice whose `owner` differs from the
opening entry's user, which makes a shift unclosable as soon as a second account
sells on it. A Barakat till is shared: accountability lives in the PIN
(custom_opened_by_staff / custom_closed_by_staff), not in the login.

These stub the invoice lookup rather than creating real POS Invoices, so they
assert the RULE, not one site's data.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


def _doc(profile="Main"):
	doc = frappe.new_doc("POS Closing Entry")
	doc.pos_profile = profile
	doc.user = "opener@example.com"
	doc.append("pos_invoices", {"pos_invoice": "ACC-PSINV-0001"})
	return doc


def _invoice(consolidated=None, profile="Main", docstatus=1, owner="someone-else@example.com"):
	return [
		frappe._dict(
			consolidated_invoice=consolidated,
			pos_profile=profile,
			docstatus=docstatus,
			owner=owner,
		)
	]


class OwnerIsNotChecked(FrappeTestCase):
	def test_accepts_an_invoice_owned_by_a_different_user(self):
		with patch("frappe.db.get_values", return_value=_invoice()):
			_doc().validate_pos_invoices()  # must not raise


class TheRemainingChecksStillFire(FrappeTestCase):
	def test_rejects_an_already_consolidated_invoice(self):
		with patch("frappe.db.get_values", return_value=_invoice(consolidated="ACC-SINV-0001")):
			with self.assertRaises(frappe.ValidationError):
				_doc().validate_pos_invoices()

	def test_rejects_an_invoice_from_another_pos_profile(self):
		with patch("frappe.db.get_values", return_value=_invoice(profile="Other")):
			with self.assertRaises(frappe.ValidationError):
				_doc().validate_pos_invoices()

	def test_rejects_an_unsubmitted_invoice(self):
		with patch("frappe.db.get_values", return_value=_invoice(docstatus=0)):
			with self.assertRaises(frappe.ValidationError):
				_doc().validate_pos_invoices()


if __name__ == "__main__":
	unittest.main()
