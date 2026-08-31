"""A return claws points back at the rate the SALE earned them, not today's rate.

Stock erpnext rebuilds the original sale's earn row on every return
(`SalesInvoice.on_submit` → `delete_loyalty_point_entry` + `make_loyalty_point_entry`)
and, in doing so, re-resolves the customer's tier **as it stands at return time**:

    lp_details = get_loyalty_program_details_with_points(..., current_transaction_amount=current_amount)
    points_earned = cint(eligible_amount / collection_factor)

The sale's own `collection_factor` is never consulted. So a customer who has since
climbed a tier gets the remainder of an OLD sale re-priced at the NEW, richer rate.
Measured on a local bench with tiers `0 → factor 10` and `1000 → factor 5`:

    sale A = 928                    →  92 points   (factor 10, total spent 0)
    sale B = 93, total spent 1021   →  +18 points  (factor 5), balance 110
    return 400 against A            →  A's row rebuilt as 528 / 5 = 105
                                    →  balance 123

The customer took 400 back in cash and gained 13 points doing it, and the trade
repeats. A FULL return hides the bug — eligible falls to 0, and 0 divided by any
factor is 0 — which is why it survived to production.

The rule this module enforces: **the rate a sale earned at is a property of that
sale.** A return recomputes how much of the sale is left, never what a unit of it is
worth. The rate is recovered from `loyalty_program_tier` on the earn row erpnext is
about to delete, and stamped back onto the row it builds, so the second return of an
invoice reads the same rate as the first.

Sales Invoices rather than POS Invoices, for the reason `test_loyalty` gives: the code
under test lives on `SalesInvoice`, and both Barakat subclasses carry the same
override. Run:

    bench --site <site> run-tests --module barakat.overrides.test_loyalty_return_tier
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from barakat.overrides.test_loyalty import _context

#: The base tier: 10 units of spend per point.
BASE_FACTOR = 10
#: The tier a customer climbs into, earning twice as fast.
HIGH_FACTOR = 5
#: Spend at which the high tier starts.
HIGH_TIER_MIN_SPENT = 1000

#: QA's numbers, kept verbatim so a failure here reads like the ticket.
SALE = 928
#: Second sale, sized to cross HIGH_TIER_MIN_SPENT: 928 + 93 = 1021.
PROMOTING_SALE = 93

PREFIX = "ZZ Return Tier"
PROGRAM_NAME = f"{PREFIX} Test Program"


def _wipe():
	"""Remove every document a previous run of these tests left behind."""
	customers = frappe.get_all(
		"Customer", filters={"customer_name": ("like", f"{PREFIX}%")}, pluck="name"
	)
	if customers:
		frappe.db.delete("Loyalty Point Entry", {"customer": ("in", customers)})
		invoices = frappe.get_all(
			"Sales Invoice",
			filters={"customer": ("in", customers)},
			fields=["name", "docstatus"],
			# Returns before the sales they are against.
			order_by="creation desc",
		)
		for invoice in invoices:
			if invoice.docstatus == 1:
				try:
					doc = frappe.get_doc("Sales Invoice", invoice.name)
					doc.flags.ignore_permissions = True
					doc.cancel()
				except Exception:
					pass
			frappe.delete_doc(
				"Sales Invoice", invoice.name, force=True, ignore_permissions=True
			)
		for customer in customers:
			frappe.delete_doc("Customer", customer, force=True, ignore_permissions=True)

	if frappe.db.exists("Loyalty Program", PROGRAM_NAME):
		frappe.delete_doc(
			"Loyalty Program", PROGRAM_NAME, force=True, ignore_permissions=True
		)
	frappe.db.commit()


class TestReturnUsesTheSalesOwnRate(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.ctx = _context()
		if cls.ctx:
			_wipe()
			cls.program = cls._make_program(cls)

	@classmethod
	def tearDownClass(cls):
		if cls.ctx:
			_wipe()
		super().tearDownClass()

	def setUp(self):
		if not self.ctx:
			self.skipTest("no company on this site has a full chart of accounts yet")

	# ── fixtures ──────────────────────────────────────────────────────────────

	def _make_program(self):
		doc = frappe.get_doc(
			{
				"doctype": "Loyalty Program",
				"loyalty_program_name": PROGRAM_NAME,
				"loyalty_program_type": "Multiple Tier Program",
				"from_date": "2020-01-01",
				"conversion_factor": 1,
				"expiry_duration": 3650,
				"company": self.ctx.company,
				"expense_account": self.ctx.expense,
				"cost_center": self.ctx.cost_center,
				"collection_rules": [
					{
						"tier_name": f"{PREFIX} Base",
						"collection_factor": BASE_FACTOR,
						"min_spent": 0,
					},
					{
						"tier_name": f"{PREFIX} High",
						"collection_factor": HIGH_FACTOR,
						"min_spent": HIGH_TIER_MIN_SPENT,
					},
				],
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		return doc.name

	def _customer(self, tag):
		doc = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"{PREFIX} {tag}",
				"customer_type": "Individual",
				"loyalty_program": self.program,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		return doc.name

	def _invoice(self, customer, amount, return_against=None):
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": customer,
				"company": self.ctx.company,
				"posting_date": today(),
				"due_date": today(),
				"debit_to": self.ctx.debit_to,
				"is_return": 1 if return_against else 0,
				"return_against": return_against,
				"update_stock": 0,
				"loyalty_program": self.program,
				"items": [
					{
						"item_code": self.ctx.item,
						"qty": -1 if return_against else 1,
						"rate": amount,
						"income_account": self.ctx.income,
						"cost_center": self.ctx.cost_center,
					}
				],
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		return doc

	def _row(self, invoice):
		"""The earn row erpnext holds for one invoice, or None."""
		rows = frappe.get_all(
			"Loyalty Point Entry",
			filters={"invoice": invoice},
			fields=["loyalty_points", "purchase_amount", "loyalty_program_tier"],
			order_by="creation asc",
		)
		return rows[0] if rows else None

	def _balance(self, customer):
		rows = frappe.get_all(
			"Loyalty Point Entry",
			filters={"customer": customer},
			pluck="loyalty_points",
		)
		return sum(rows)

	def _promoted_customer(self, tag):
		"""A customer with sale A at the base rate, then promoted into the high tier.

		Returns (customer, sale A). Asserts the setup itself, so a failure in the
		tests below is never really a failure to reach the high tier.
		"""
		customer = self._customer(tag)
		sale = self._invoice(customer, SALE)
		self.assertEqual(self._row(sale.name).loyalty_points, SALE // BASE_FACTOR)
		self._invoice(customer, PROMOTING_SALE)
		self.assertEqual(
			frappe.db.get_value("Customer", customer, "loyalty_program_tier"),
			f"{PREFIX} High",
			"setup did not reach the high tier",
		)
		return customer, sale

	# ── the rule ──────────────────────────────────────────────────────────────

	def test_a_partial_return_claws_back_at_the_rate_the_sale_earned(self):
		customer, sale = self._promoted_customer("partial")
		self._invoice(customer, 400, return_against=sale.name)
		# 528 of the sale is left, and the sale earned at the base rate.
		self.assertEqual(self._row(sale.name).loyalty_points, 528 // BASE_FACTOR)

	def test_a_partial_return_never_raises_the_balance(self):
		customer, sale = self._promoted_customer("no-raise")
		before = self._balance(customer)
		self._invoice(customer, 400, return_against=sale.name)
		self.assertLess(self._balance(customer), before)

	def test_successive_partial_returns_stay_on_the_sales_rate(self):
		customer, sale = self._promoted_customer("successive")
		self._invoice(customer, 300, return_against=sale.name)
		self.assertEqual(self._row(sale.name).loyalty_points, 628 // BASE_FACTOR)
		self._invoice(customer, 300, return_against=sale.name)
		self.assertEqual(self._row(sale.name).loyalty_points, 328 // BASE_FACTOR)

	def test_the_rebuilt_row_keeps_the_tier_the_sale_earned_at(self):
		"""The stamp is what the SECOND return reads, so it has to survive the first."""
		customer, sale = self._promoted_customer("stamp")
		self._invoice(customer, 300, return_against=sale.name)
		self.assertEqual(self._row(sale.name).loyalty_program_tier, f"{PREFIX} Base")

	def test_a_full_return_still_earns_nothing(self):
		customer, sale = self._promoted_customer("full")
		self._invoice(customer, SALE, return_against=sale.name)
		self.assertEqual(self._row(sale.name).loyalty_points, 0)

	def test_the_promoting_sale_keeps_the_points_it_earned(self):
		"""Only the RETURNED sale is recomputed. Nothing re-rates a sale left alone."""
		customer, sale = self._promoted_customer("untouched")
		self._invoice(customer, SALE, return_against=sale.name)
		self.assertEqual(
			frappe.get_all(
				"Loyalty Point Entry",
				filters={"customer": customer, "purchase_amount": PROMOTING_SALE},
				pluck="loyalty_points",
			),
			[PROMOTING_SALE // HIGH_FACTOR],
		)

	# ── regressions: nothing above may change the simple cases ────────────────

	def test_a_return_without_a_tier_change_is_unaffected(self):
		customer = self._customer("flat")
		sale = self._invoice(customer, 500)
		self.assertEqual(self._row(sale.name).loyalty_points, 50)
		self._invoice(customer, 200, return_against=sale.name)
		self.assertEqual(self._row(sale.name).loyalty_points, 30)

	def test_a_sale_still_earns_at_the_tier_it_crosses_into(self):
		"""The sale that crosses the threshold earns at the NEW tier, as erpnext does.

		This is the behaviour the fix must NOT change: it is about the sale being rung
		up, not about a return re-pricing an old one.
		"""
		customer = self._customer("crossing")
		self._invoice(customer, SALE)
		promoting = self._invoice(customer, PROMOTING_SALE)
		self.assertEqual(
			self._row(promoting.name).loyalty_points, PROMOTING_SALE // HIGH_FACTOR
		)

	def test_the_spend_recorded_by_a_partial_return_is_unchanged(self):
		"""`align_loyalty_spend`'s rule still holds: the row carries bill less returns."""
		customer, sale = self._promoted_customer("spend")
		self._invoice(customer, 400, return_against=sale.name)
		self.assertEqual(flt(self._row(sale.name).purchase_amount, 2), 528)


class TestRepriceReturnedEarnRowsPatch(FrappeTestCase):
	"""`barakat.patches.reprice_returned_earn_rows` repairs rows written before the fix.

	The rows are corrupted here to exactly what the old code produced — the points
	recomputed at the tier the customer had reached by return time, and that tier
	stamped over the sale's own — so the patch is exercised against real invoices
	rather than against a hand-built fixture.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.ctx = _context()
		if cls.ctx:
			_wipe()
			cls.program = TestReturnUsesTheSalesOwnRate._make_program(cls)

	@classmethod
	def tearDownClass(cls):
		if cls.ctx:
			_wipe()
		super().tearDownClass()

	def setUp(self):
		if not self.ctx:
			self.skipTest("no company on this site has a full chart of accounts yet")

	_customer = TestReturnUsesTheSalesOwnRate._customer
	_invoice = TestReturnUsesTheSalesOwnRate._invoice
	_row = TestReturnUsesTheSalesOwnRate._row
	_balance = TestReturnUsesTheSalesOwnRate._balance

	def _corrupt(self, invoice, points, tier):
		"""Put an invoice's earn row back to what the pre-fix code would have left."""
		row = self._row(invoice)
		frappe.db.set_value(
			"Loyalty Point Entry",
			frappe.get_all("Loyalty Point Entry", filters={"invoice": invoice}, pluck="name")[0],
			{"loyalty_points": points, "loyalty_program_tier": tier},
		)
		return row

	def _run(self):
		from barakat.patches.reprice_returned_earn_rows import execute

		execute()

	def test_it_reprices_a_row_the_old_code_inflated(self):
		customer = self._customer("patch-inflated")
		sale = self._invoice(customer, SALE)
		self._invoice(customer, PROMOTING_SALE)
		self._invoice(customer, 400, return_against=sale.name)
		self._corrupt(sale.name, 105, f"{PREFIX} High")

		self._run()

		self.assertEqual(self._row(sale.name).loyalty_points, 528 // BASE_FACTOR)
		self.assertEqual(self._row(sale.name).loyalty_program_tier, f"{PREFIX} Base")

	def test_it_uses_the_spend_as_it_stood_when_the_sale_was_rung_up(self):
		"""Replaying with TODAY's amounts is not good enough, and this proves it.

		Sale B genuinely earned at the high tier — 600 already spent plus its own 600
		crosses 1000. By the time both sales have been partly returned the ledger adds
		up to less than the threshold, so a repair that trusted current amounts would
		wrongly re-price B down to the base rate.
		"""
		customer = self._customer("patch-history")
		first = self._invoice(customer, 600)
		second = self._invoice(customer, 600)
		self.assertEqual(self._row(second.name).loyalty_program_tier, f"{PREFIX} High")
		self._invoice(customer, 300, return_against=first.name)
		self._invoice(customer, 300, return_against=second.name)
		self._corrupt(second.name, 999, f"{PREFIX} Base")

		self._run()

		# 300 of sale B is left, and B earned at the high rate: 300 / 5 = 60.
		self.assertEqual(self._row(second.name).loyalty_points, 300 // HIGH_FACTOR)
		self.assertEqual(self._row(second.name).loyalty_program_tier, f"{PREFIX} High")

	def test_an_invoice_with_no_return_is_left_alone(self):
		customer = self._customer("patch-untouched")
		sale = self._invoice(customer, SALE)
		self._invoice(customer, PROMOTING_SALE)
		before = self._row(sale.name).loyalty_points

		self._run()

		self.assertEqual(self._row(sale.name).loyalty_points, before)

	def test_running_it_twice_changes_nothing(self):
		customer = self._customer("patch-idempotent")
		sale = self._invoice(customer, SALE)
		self._invoice(customer, PROMOTING_SALE)
		self._invoice(customer, 400, return_against=sale.name)
		self._corrupt(sale.name, 105, f"{PREFIX} High")

		self._run()
		once = self._row(sale.name).loyalty_points
		self._run()

		self.assertEqual(self._row(sale.name).loyalty_points, once)

	def test_it_restamps_a_customer_left_on_a_tier_the_ledger_no_longer_supports(self):
		customer = self._customer("patch-restamp")
		sale = self._invoice(customer, SALE)
		self._invoice(customer, PROMOTING_SALE)
		self._invoice(customer, 400, return_against=sale.name)
		self._corrupt(sale.name, 105, f"{PREFIX} High")
		frappe.db.set_value(
			"Customer", customer, "loyalty_program_tier", f"{PREFIX} High"
		)

		self._run()

		self.assertEqual(
			frappe.db.get_value("Customer", customer, "loyalty_program_tier"),
			f"{PREFIX} Base",
		)
