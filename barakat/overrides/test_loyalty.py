"""Tests for the loyalty total-spend rule in `barakat.overrides.loyalty`.

These are INTEGRATION tests: each one submits real Sales Invoices and lets erpnext's
own loyalty code write the ledger, then asks `get_loyalty_details` — the function the
POS, the Admin Panel and the tier stamp all read through — what the customer has
spent. Nothing calls the alignment directly, so a test only passes if the doctype
override actually fires on submit.

That is deliberate. The rule is one line to state (record each bill once, on the earn
row) and the risk is not in stating it — it is in erpnext's four writers disagreeing:
`make_loyalty_point_entry` writes the cash-funded part, `apply_loyalty_points` writes
the whole bill once per earn entry it consumes, a return deletes and rebuilds the
first, and `POSInvoice` runs them in the opposite order to `SalesInvoice`. Unit-testing
the arithmetic would exercise none of that.

Sales Invoices rather than POS Invoices: the loyalty code under test lives on
`SalesInvoice` and both classes reach it the same way, while standing up a POS Invoice
needs an opening entry, a profile and a warehouse for every assertion. The one thing
that differs — `BarakatPOSInvoice` inheriting from erpnext's `POSInvoice` rather than
from `BarakatSalesInvoice`, so it needs its own copy of the override — is a class
declaration, checked here directly.

Points must be earned before they can be redeemed, so every redeeming test opens with
a plain sale whose value is part of the expected total. FrappeTestCase rolls each test
back, so no invoice, customer or program survives the run.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from erpnext.accounts.doctype.loyalty_program.loyalty_program import get_loyalty_details

#: Tier threshold, chosen so `test_a_tier_is_not_bought_by_double_counting`'s customer
#: lands under it counted once and over it on the old double-counted reading.
HIGH_TIER_MIN_SPENT = 550

#: Everything these tests create is named with this prefix and wiped either side of a
#: run. FrappeTestCase's per-test rollback is not relied on: submitting an invoice can
#: commit, and on a bench where these are run against a working site the leftovers
#: would collide with the next run and, worse, stay behind in someone's data.
PREFIX = "ZZ Spend"
PROGRAM_NAME = f"{PREFIX} Test Program"


def _wipe():
	"""Remove every document a previous run of these tests left behind."""
	customers = frappe.get_all(
		"Customer", filters={"customer_name": ("like", f"{PREFIX}%")}, pluck="name"
	)
	if customers:
		# Cleared first and directly: cancelling an invoice whose earned points were
		# redeemed elsewhere is refused by erpnext, and these rows are the thing under
		# test rather than something worth preserving.
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


def _context():
	"""A company on the site with the accounts these tests need, or None.

	Income is found by `root_type`, not `account_type`: the Barakat charts leave
	account_type unset on their income leaves, so nothing matches "Income Account".
	"""
	for company in frappe.get_all("Company", pluck="name"):
		debit_to = frappe.db.get_value(
			"Account",
			{"company": company, "account_type": "Receivable", "is_group": 0},
			"name",
		)
		income = frappe.db.get_value(
			"Account", {"company": company, "root_type": "Income", "is_group": 0}, "name"
		)
		expense = frappe.db.get_value(
			"Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name"
		)
		cost_center = frappe.db.get_value("Company", company, "cost_center")
		item = frappe.db.get_value("Item", {"disabled": 0, "is_sales_item": 1}, "name")
		if debit_to and income and expense and cost_center and item:
			return frappe._dict(
				company=company,
				debit_to=debit_to,
				income=income,
				expense=expense,
				cost_center=cost_center,
				item=item,
			)
	return None


class TestLoyaltySpendIsRecordedOnce(FrappeTestCase):
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
					{"tier_name": "ZZ Base", "collection_factor": 1, "min_spent": 0},
					{
						"tier_name": "ZZ High",
						"collection_factor": 1,
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

	def _invoice(self, customer, amount, redeem_points=0, return_against=None):
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
		if redeem_points:
			doc.redeem_loyalty_points = 1
			doc.loyalty_points = redeem_points
			doc.loyalty_redemption_account = self.ctx.expense
			doc.loyalty_redemption_cost_center = self.ctx.cost_center
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		return doc

	def _total_spent(self, customer):
		details = get_loyalty_details(
			customer, self.program, company=self.ctx.company, include_expired_entry=True
		)
		return flt(details.get("total_spent"), 2)

	def _rows(self, customer):
		return frappe.get_all(
			"Loyalty Point Entry",
			filters={"customer": customer},
			fields=["name", "invoice", "loyalty_points", "purchase_amount"],
			order_by="creation asc",
		)

	# ── the rule ──────────────────────────────────────────────────────────────

	def test_a_plain_sale_counts_its_bill(self):
		customer = self._customer("plain")
		self._invoice(customer, 100)
		self.assertEqual(self._total_spent(customer), 100)

	def test_a_sale_paid_partly_with_points_counts_once(self):
		"""The reported defect, to the shekel.

		ACC-PSINV-2026-00575: three invoices of ₪92.80, ₪23.20 and ₪23.20, the last
		settled with ₪10 of points, were shown as ₪152.40 of spend. Stock erpnext put
		₪23.20 on the redemption row and ₪13.20 on the earn row and summed both.
		"""
		customer = self._customer("partial")
		self._invoice(customer, 92.80)
		self._invoice(customer, 23.20)
		self._invoice(customer, 23.20, redeem_points=10)
		self.assertEqual(self._total_spent(customer), 139.20)

	def test_a_bill_settled_entirely_with_points_still_counts(self):
		"""Eligible amount 0, so erpnext's earn row carries nothing — the bill still happened."""
		customer = self._customer("full-points")
		self._invoice(customer, 200)
		self._invoice(customer, 50, redeem_points=50)
		self.assertEqual(self._total_spent(customer), 250)

	def test_a_redemption_spanning_several_earn_entries(self):
		"""`apply_loyalty_points` writes one row per earn entry it eats through.

		Live shape: ACC-PSINV-2026-00553 redeemed across ten entries and so carried ten
		redemption rows of ₪1,044 each — ₪10,440 recorded for one ₪1,044 sale. Every one
		of them has to end up carrying nothing, not just the first.
		"""
		customer = self._customer("fifo")
		for _ in range(4):
			self._invoice(customer, 25)
		self._invoice(customer, 80, redeem_points=80)

		redemptions = [r for r in self._rows(customer) if r.loyalty_points < 0]
		self.assertGreaterEqual(len(redemptions), 4, "expected one row per entry consumed")
		self.assertEqual(sum(flt(r.purchase_amount) for r in redemptions), 0)
		self.assertEqual(self._total_spent(customer), 180)

	# ── returns ───────────────────────────────────────────────────────────────

	def test_a_fully_returned_sale_counts_nothing(self):
		customer = self._customer("full-return")
		self._invoice(customer, 500)
		sale = self._invoice(customer, 120, redeem_points=20)
		self._invoice(customer, 120, return_against=sale.name)
		self.assertEqual(self._total_spent(customer), 500)

	def test_a_partly_returned_sale_counts_the_kept_part(self):
		customer = self._customer("partial-return")
		self._invoice(customer, 500)
		sale = self._invoice(customer, 100, redeem_points=10)
		self._invoice(customer, 30, return_against=sale.name)
		self.assertEqual(self._total_spent(customer), 570)

	def test_successive_returns_each_come_off(self):
		customer = self._customer("two-returns")
		self._invoice(customer, 500)
		sale = self._invoice(customer, 100, redeem_points=10)
		self._invoice(customer, 30, return_against=sale.name)
		self._invoice(customer, 20, return_against=sale.name)
		self.assertEqual(self._total_spent(customer), 550)

	# ── the money consequence ─────────────────────────────────────────────────

	def test_a_tier_is_not_bought_by_double_counting(self):
		"""Why this is not a display bug: the tier supplies `collection_factor`.

		Counted once these two bills come to ₪500, under the ₪550 threshold. Stock
		erpnext read ₪300 + ₪100 + ₪200 = ₪600 and promoted the customer, after which
		they earned points faster than they had paid for.
		"""
		customer = self._customer("tier")
		self._invoice(customer, 300)
		self._invoice(customer, 200, redeem_points=100)
		self.assertEqual(self._total_spent(customer), 500)
		self.assertEqual(
			frappe.db.get_value("Customer", customer, "loyalty_program_tier"), "ZZ Base"
		)

	def test_points_earned_are_unchanged(self):
		"""Paying with points still earns on the cash part only.

		`make_loyalty_point_entry` derives points from a local `eligible_amount` and
		never re-reads `purchase_amount`, so moving the bill onto the earn row must not
		move the points with it: ₪100 settled with ₪10 of points earns 90, not 100.
		"""
		customer = self._customer("earn")
		self._invoice(customer, 500)
		sale = self._invoice(customer, 100, redeem_points=10)
		earn = [
			r for r in self._rows(customer) if r.invoice == sale.name and r.loyalty_points > 0
		]
		self.assertEqual(len(earn), 1)
		self.assertEqual(earn[0].loyalty_points, 90)
		self.assertEqual(flt(earn[0].purchase_amount, 2), 100)

	# ── idempotence and no-ops ────────────────────────────────────────────────

	def test_aligning_twice_changes_nothing(self):
		from barakat.overrides.loyalty import align_invoice_spend

		customer = self._customer("idempotent")
		self._invoice(customer, 100)
		sale = self._invoice(customer, 60, redeem_points=10)
		before = self._total_spent(customer)

		self.assertEqual(before, 160)
		self.assertEqual(align_invoice_spend(sale.doctype, sale.name), 0)
		self.assertEqual(self._total_spent(customer), before)

	def test_an_invoice_with_no_ledger_rows_is_untouched(self):
		from barakat.overrides.loyalty import align_loyalty_spend

		customer = self._customer("no-program")
		frappe.db.set_value("Customer", customer, "loyalty_program", None)
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": customer,
				"company": self.ctx.company,
				"posting_date": today(),
				"due_date": today(),
				"debit_to": self.ctx.debit_to,
				"update_stock": 0,
				"items": [
					{
						"item_code": self.ctx.item,
						"qty": 1,
						"rate": 40,
						"income_account": self.ctx.income,
						"cost_center": self.ctx.cost_center,
					}
				],
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		self.assertEqual(align_loyalty_spend(doc), 0)

	def test_the_pos_invoice_class_carries_its_own_copy(self):
		"""`BarakatPOSInvoice` extends erpnext's `POSInvoice`, NOT `BarakatSalesInvoice`.

		So it inherits none of the Sales Invoice override, and every Barakat sale is a
		POS Invoice. If this ever starts failing because the class was refactored, check
		that the override still reaches the POS path before deleting the assertion.
		"""
		from barakat.overrides.pos_invoice import BarakatPOSInvoice
		from barakat.overrides.sales_invoice import BarakatSalesInvoice

		self.assertFalse(issubclass(BarakatPOSInvoice, BarakatSalesInvoice))
		self.assertIn("on_submit", vars(BarakatPOSInvoice))
		self.assertIn("on_submit", vars(BarakatSalesInvoice))


class TestAlignLoyaltyPurchaseAmountPatch(FrappeTestCase):
	"""The backfill, against a ledger rewritten to exactly what erpnext used to leave."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.ctx = _context()
		if cls.ctx:
			_wipe()
			cls.program = TestLoyaltySpendIsRecordedOnce._make_program(cls)

	@classmethod
	def tearDownClass(cls):
		if cls.ctx:
			_wipe()
		super().tearDownClass()

	def setUp(self):
		if not self.ctx:
			self.skipTest("no company on this site has a full chart of accounts yet")
		helper = TestLoyaltySpendIsRecordedOnce
		self._customer = lambda tag: helper._customer(self, tag)
		self._invoice = lambda *a, **k: helper._invoice(self, *a, **k)
		self._total_spent = lambda c: helper._total_spent(self, c)
		self._rows = lambda c: helper._rows(self, c)

	def test_it_restores_the_bills_and_then_leaves_them_alone(self):
		from barakat.patches.align_loyalty_purchase_amount import execute

		customer = self._customer("patch")
		self._invoice(customer, 92.80)
		sale = self._invoice(customer, 23.20, redeem_points=10)

		# Put back exactly what erpnext wrote before the override: the whole bill on the
		# redemption row, the cash-funded part on the earn row.
		for row in self._rows(customer):
			if row.invoice != sale.name:
				continue
			stale = 23.20 if row.loyalty_points < 0 else 13.20
			frappe.db.set_value("Loyalty Point Entry", row.name, "purchase_amount", stale)
		self.assertEqual(self._total_spent(customer), 129.20)

		execute()
		self.assertEqual(self._total_spent(customer), 116.00)

		# Idempotent: a second pass finds nothing left to move.
		execute()
		self.assertEqual(self._total_spent(customer), 116.00)
