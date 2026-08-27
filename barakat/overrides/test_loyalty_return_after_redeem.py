"""A sale can be returned after the points it earned have been spent (0001-610).

Stock erpnext cannot. `SalesInvoice.on_submit` / `POSInvoice.on_submit` recompute the
ORIGINAL sale's earn row on every return by deleting it and building it again, and
`delete_loyalty_point_entry` refuses to delete a row that a redemption points at:

    "{} can't be cancelled since the Loyalty Points earned has been redeemed.
     First cancel the {} No {}"

At a till that is not an instruction anyone can follow — the invoice it names is the
one the customer SPENT the points on, and they left with those goods. And because
redemptions are allocated FIFO (`apply_loyalty_points` walks earn rows by expiry), the
link only ever lands on the OLDEST sale, so exactly one invoice in the customer's
history is un-returnable while every other one works. That is the shape QA reported.

`barakat.overrides.loyalty.release_redemptions_against` detaches those redemption rows
before the delete. The rows survive — the points really were spent — they simply stop
naming the row being removed. Nothing reads `redeem_against` for a balance, so the
customer's number is unchanged by the detach itself; only the return's own claw-back
moves it.

These tests use Sales Invoices for the same reason `test_loyalty` does: the loyalty
code under test lives on `SalesInvoice`, `POSInvoice` inherits it, and both Barakat
subclasses carry the same override. Run:

    bench --site <site> run-tests --module barakat.overrides.test_loyalty_return_after_redeem
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from erpnext.accounts.doctype.loyalty_program.loyalty_program import get_loyalty_details

from barakat.overrides.test_loyalty import _context

#: 10 units of spend per point, so QA's numbers survive verbatim: a 40 sale earns 4.
COLLECTION_FACTOR = 10

PREFIX = "ZZ Redeem"
PROGRAM_NAME = f"{PREFIX} Test Program"


def _wipe():
	"""Remove everything a previous run left behind.

	Loyalty rows go first and go directly. Cancelling an invoice whose points were
	redeemed is the very thing these tests exercise, and a half-cleaned run must not
	depend on the fix it is testing to clean up after itself.
	"""
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


class TestReturnAfterRedeem(FrappeTestCase):
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
				"loyalty_program_type": "Single Tier Program",
				"from_date": "2020-01-01",
				# 1 point buys 1 unit of currency back, as in the report.
				"conversion_factor": 1,
				"expiry_duration": 3650,
				"company": self.ctx.company,
				"expense_account": self.ctx.expense,
				"cost_center": self.ctx.cost_center,
				"collection_rules": [
					{
						"tier_name": "ZZ Redeem Base",
						"collection_factor": COLLECTION_FACTOR,
						"min_spent": 0,
					}
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

	def _sale(self, customer, amount, redeem_points=0):
		return self._invoice(customer, amount, redeem_points=redeem_points)

	def _return(self, sale, amount=None):
		"""Return `amount` of `sale`; the whole sale when amount is None."""
		return self._invoice(
			sale.customer,
			sale.grand_total if amount is None else amount,
			return_against=sale.name,
		)

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

	# ── reading the ledger ────────────────────────────────────────────────────

	def _balance(self, customer):
		details = get_loyalty_details(
			customer, self.program, company=self.ctx.company, include_expired_entry=True
		)
		return int(flt(details.get("loyalty_points")))

	def _rows(self, customer):
		return frappe.get_all(
			"Loyalty Point Entry",
			filters={"customer": customer},
			fields=["name", "invoice", "loyalty_points", "redeem_against"],
			order_by="creation asc",
		)

	def _rows_for(self, customer, invoice):
		return [r for r in self._rows(customer) if r.invoice == invoice]

	def _three_sales_then_redeem(self, tag):
		"""QA's exact setup: three 40 sales (4 points each), then 2 points spent.

		Returns the three sales. Balance afterwards is 12 earned - 2 spent = 10, and
		the 2-point redemption is attached FIFO to the FIRST sale's earn row.
		"""
		customer = self._customer(tag)
		sales = [self._sale(customer, 40) for _ in range(3)]
		self.assertEqual(self._balance(customer), 12)
		self._sale(customer, 2, redeem_points=2)
		self.assertEqual(self._balance(customer), 10)
		return customer, sales

	# ── the reported defect ───────────────────────────────────────────────────

	def test_the_oldest_sale_can_be_returned_after_its_points_were_spent(self):
		"""0001-610, to the point: this is the return that used to throw."""
		customer, sales = self._three_sales_then_redeem("oldest")

		self._return(sales[0])

		# 8 still earned across the two sales that stand, less the 2 spent.
		self.assertEqual(self._balance(customer), 6)
		self.assertEqual(sum(r.loyalty_points for r in self._rows_for(customer, sales[0].name)), 0)

	def test_the_redemption_survives_the_return(self):
		"""Detached, not deleted. The customer really did spend those points."""
		customer, sales = self._three_sales_then_redeem("survives")
		spent_before = [r for r in self._rows(customer) if r.loyalty_points < 0]
		self.assertEqual(len(spent_before), 1)
		self.assertEqual(spent_before[0].loyalty_points, -2)
		self.assertIsNotNone(spent_before[0].redeem_against)

		self._return(sales[0])

		spent_after = [r for r in self._rows(customer) if r.loyalty_points < 0]
		self.assertEqual(len(spent_after), 1)
		self.assertEqual(spent_after[0].name, spent_before[0].name)
		self.assertEqual(spent_after[0].loyalty_points, -2)
		# The one thing that changed: it no longer names a row that is gone.
		self.assertIsNone(spent_after[0].redeem_against)

	def test_the_other_two_sales_still_return_cleanly(self):
		"""These never carried the link and never failed — guard the regression."""
		customer, sales = self._three_sales_then_redeem("others")

		self._return(sales[1])
		self.assertEqual(self._balance(customer), 6)
		self._return(sales[2])
		self.assertEqual(self._balance(customer), 2)

	def test_every_sale_in_the_history_can_be_returned(self):
		"""All three, oldest first — the case that used to fail on the first move."""
		customer, sales = self._three_sales_then_redeem("all")

		for sale in sales:
			self._return(sale)

		# Nothing earned is left standing; the 2 spent remain spent.
		self.assertEqual(self._balance(customer), -2)

	# ── the balance is allowed to go below zero ───────────────────────────────

	def test_a_balance_may_go_negative_when_the_points_were_over_spent(self):
		"""The customer owes points, and the ledger says so rather than rounding it away.

		They cannot redeem again until they earn back past zero — `validate_loyalty_points`
		compares the request against this total — which is what stops the cycle repeating.
		Whether to hand over the cash at all is a decision for the till, not for here.
		"""
		customer = self._customer("negative")
		sale = self._sale(customer, 40)
		self.assertEqual(self._balance(customer), 4)
		self._sale(customer, 4, redeem_points=4)
		self.assertEqual(self._balance(customer), 0)

		self._return(sale)

		self.assertEqual(self._balance(customer), -4)

	def test_a_negative_balance_blocks_the_next_redemption(self):
		customer = self._customer("blocked")
		sale = self._sale(customer, 40)
		self._sale(customer, 4, redeem_points=4)
		self._return(sale)
		self.assertEqual(self._balance(customer), -4)

		with self.assertRaises(frappe.ValidationError):
			self._sale(customer, 50, redeem_points=1)

	# ── no earn row may end up owing more than it holds ───────────────────────

	def test_a_partial_return_leaves_no_row_over_attributed(self):
		"""The landmine underneath the throw, which the fix has to clear too.

		`apply_loyalty_points` computes `available = row.points - redeemed_against_it`
		and, when that is negative, writes `-1 * available` — a POSITIVE entry, points
		from nothing. Cutting an earn row below what is already redeemed against it is
		exactly what erpnext's delete-and-recreate does on a PARTIAL return; today only
		the throw keeps it unreachable. Detaching first means no row is ever left owing
		more than it holds.
		"""
		customer = self._customer("partial")
		sale = self._sale(customer, 40)
		self._sale(customer, 4, redeem_points=4)

		# Return 30 of the 40: the sale's earn falls from 4 points to 1, which is BELOW
		# the 4 that were redeemed against it.
		self._return(sale, amount=30)
		self.assertEqual(self._balance(customer), -3)

		for row in self._rows(customer):
			if row.loyalty_points <= 0 or not row.redeem_against:
				continue
			against = frappe.get_all(
				"Loyalty Point Entry",
				filters={"redeem_against": row.name},
				pluck="loyalty_points",
			)
			self.assertGreaterEqual(row.loyalty_points + sum(against), 0)

	def test_a_later_redemption_after_a_return_mints_no_points(self):
		"""End-to-end guard on the same landmine: earn, spend, return, earn, spend."""
		customer = self._customer("mint")
		sale = self._sale(customer, 40)
		self._sale(customer, 4, redeem_points=4)
		self._return(sale, amount=30)
		self.assertEqual(self._balance(customer), -3)

		# Earn back over zero, then spend again.
		self._sale(customer, 100)
		self.assertEqual(self._balance(customer), 7)
		self._sale(customer, 5, redeem_points=5)

		self.assertEqual(self._balance(customer), 2)
		# The tell-tale of the minting bug is a POSITIVE row on a redeeming invoice.
		# Zero is normal and not a symptom: a sale settled entirely with points has an
		# eligible amount of 0, so its own earn row is worth 0.
		redeeming = frappe.get_all(
			"Sales Invoice",
			filters={"customer": customer, "redeem_loyalty_points": 1, "docstatus": 1},
			pluck="name",
		)
		for row in self._rows(customer):
			if row.invoice in redeeming:
				self.assertLessEqual(row.loyalty_points, 0, f"{row.name} minted points")

	# ── unchanged behaviour ───────────────────────────────────────────────────

	def test_a_sale_with_no_redemption_against_it_returns_as_before(self):
		customer = self._customer("plain")
		sale = self._sale(customer, 40)
		self.assertEqual(self._balance(customer), 4)

		self._return(sale)

		self.assertEqual(self._balance(customer), 0)
		# erpnext rebuilds the row rather than leaving none: a fully returned sale has
		# an eligible amount of 0, so the recreated row is worth 0 points.
		self.assertEqual(
			sum(r.loyalty_points for r in self._rows_for(customer, sale.name)), 0
		)

	def test_a_partial_return_still_claws_back_only_its_share(self):
		customer = self._customer("share")
		sale = self._sale(customer, 40)

		self._return(sale, amount=10)

		# 30 of the 40 still stands, at 10 per point.
		self.assertEqual(self._balance(customer), 3)

	def test_cancelling_a_sale_whose_points_were_spent_is_allowed(self):
		"""The other caller of the same guard — a desk-side cancel, not a return."""
		customer, sales = self._three_sales_then_redeem("cancel")

		doc = frappe.get_doc("Sales Invoice", sales[0].name)
		doc.flags.ignore_permissions = True
		doc.cancel()

		self.assertEqual(self._balance(customer), 6)

	def test_a_customer_with_no_loyalty_rows_is_untouched(self):
		"""`release_redemptions_against` must be a no-op with nothing to release."""
		from barakat.overrides.loyalty import release_redemptions_against

		self.assertEqual(
			release_redemptions_against("Sales Invoice", "ZZ-does-not-exist"), 0
		)
