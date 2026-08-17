"""On-bench tests for barakat.api.shift.get_shift_summary.

Run on a site:
    bench --site <site> run-tests --module barakat.api.test_shift_summary
Not runnable on the Windows dev box (imports `frappe`).

This endpoint is what a till falls back on when its local database is gone and
it still has to close the shift. It compared `mode_of_payment == "Cash"`
literally, so a company whose cash mode is named `نقدي` got an opening balance of
0 and cash sales of 0 — and the cashier was told they were over by the whole
day's takings. Cash is decided by the Mode of Payment's TYPE here, as it already
was on the POS side.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from barakat.api import shift as shift_api

ARABIC_CASH = "نقدي"
VISA = "Visa"


def opening(balance_mode=ARABIC_CASH, amount=100.0):
	return frappe._dict(
		name="POS-OPE-0001",
		pos_profile="Main",
		period_start_date="2026-08-17 09:00:00",
		balance_details=[
			frappe._dict(mode_of_payment=balance_mode, opening_amount=amount)
		],
	)


def payment_row(name, mode, amount, is_return=0):
	return frappe._dict(
		name=name, is_return=is_return, mode_of_payment=mode, amount=amount
	)


class TestGetShiftSummary(FrappeTestCase):
	def run_summary(self, rows, cash_modes, opening_doc=None):
		"""Drive the endpoint with fixed invoice rows and no journal entries."""
		calls = {"n": 0}

		def fake_sql(*args, **kwargs):
			calls["n"] += 1
			# First query is the invoice tenders, second the journal entries.
			return rows if calls["n"] == 1 else []

		with patch.object(
			shift_api.frappe, "get_doc", return_value=opening_doc or opening()
		), patch.object(
			shift_api, "get_cash_modes", return_value=set(cash_modes)
		), patch.object(
			shift_api.frappe.db, "sql", side_effect=fake_sql
		), patch.object(
			shift_api.frappe.db, "get_value", return_value="Cash - S1"
		):
			return shift_api.get_shift_summary("POS-OPE-0001")

	def test_arabic_cash_mode_is_counted_as_cash(self):
		result = self.run_summary(
			[
				payment_row("INV-1", ARABIC_CASH, 25.0),
				payment_row("INV-2", VISA, 475.0),
			],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(result["opening_cash"], 100.0)
		self.assertEqual(result["cash_sales"], 25.0)
		self.assertEqual(result["expected_total"], 125.0)

	def test_card_takings_are_reported_but_never_enter_the_drawer(self):
		result = self.run_summary(
			[
				payment_row("INV-1", ARABIC_CASH, 25.0),
				payment_row("INV-2", VISA, 475.0),
			],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(
			result["non_cash_sales"], [{"mode_of_payment": VISA, "amount": 475.0}]
		)
		self.assertEqual(result["non_cash_total"], 475.0)
		self.assertEqual(result["total_sales"], 500.0)
		# The drawer is untouched by any of it.
		self.assertEqual(result["expected_total"], 125.0)

	def test_a_mode_named_cash_but_typed_bank_is_not_cash(self):
		result = self.run_summary(
			[payment_row("INV-1", "Cash Transfer", 90.0)],
			cash_modes=[ARABIC_CASH],
			opening_doc=opening(balance_mode=ARABIC_CASH, amount=0.0),
		)
		self.assertEqual(result["cash_sales"], 0.0)
		self.assertEqual(result["non_cash_total"], 90.0)

	def test_a_refund_leaves_the_drawer(self):
		result = self.run_summary(
			[
				payment_row("INV-1", ARABIC_CASH, 50.0),
				payment_row("INV-2", ARABIC_CASH, 20.0, is_return=1),
			],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(result["cash_refunds"], 20.0)
		self.assertEqual(result["expected_total"], 130.0)

	def test_a_card_refund_is_kept_out_of_the_sales_breakdown(self):
		result = self.run_summary(
			[payment_row("INV-1", VISA, 30.0, is_return=1)],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(result["non_cash_sales"], [])
		self.assertEqual(result["expected_total"], 100.0)

	def test_modes_are_aggregated_and_ordered_biggest_first(self):
		result = self.run_summary(
			[
				payment_row("INV-1", VISA, 100.0),
				payment_row("INV-2", VISA, 200.0),
				payment_row("INV-3", "Mastercard", 50.0),
			],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(
			[row["mode_of_payment"] for row in result["non_cash_sales"]],
			[VISA, "Mastercard"],
		)
		self.assertEqual(result["non_cash_sales"][0]["amount"], 300.0)

	def test_an_invoice_with_no_tender_still_counts_as_an_order(self):
		# The LEFT JOIN yields a null mode for an invoice with no payment rows.
		result = self.run_summary(
			[payment_row("INV-1", None, 0.0)],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(result["orders_count"], 1)
		self.assertEqual(result["cash_sales"], 0.0)

	def test_an_invoice_is_counted_once_however_many_tenders_it_has(self):
		result = self.run_summary(
			[
				payment_row("INV-1", ARABIC_CASH, 30.0),
				payment_row("INV-1", VISA, 70.0),
			],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(result["orders_count"], 1)
		self.assertEqual(result["cash_sales"], 30.0)
		self.assertEqual(result["total_sales"], 100.0)

	def test_a_return_is_not_an_order(self):
		result = self.run_summary(
			[
				payment_row("INV-1", ARABIC_CASH, 50.0),
				payment_row("INV-2", ARABIC_CASH, 10.0, is_return=1),
			],
			cash_modes=[ARABIC_CASH],
		)
		self.assertEqual(result["orders_count"], 1)

	def test_a_till_whose_profile_has_no_cash_mode_reports_no_cash(self):
		# Honest rather than guessed: nothing is promoted into the drawer.
		result = self.run_summary(
			[payment_row("INV-1", VISA, 60.0)],
			cash_modes=[],
			opening_doc=opening(balance_mode=VISA, amount=0.0),
		)
		self.assertEqual(result["opening_cash"], 0.0)
		self.assertEqual(result["cash_sales"], 0.0)
		self.assertEqual(result["expected_total"], 0.0)
		self.assertEqual(result["total_sales"], 60.0)


if __name__ == "__main__":
	unittest.main()
