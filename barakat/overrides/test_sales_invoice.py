"""Tests for the POS loyalty GL overrides in `barakat.overrides.sales_invoice`.

These are UNIT tests: they build a Sales Invoice in memory, call the override's
GL-building methods directly, and inspect the ledger lines those methods append.
Nothing is saved or submitted, so no invoice, no GL Entry and no committed data is
left on the site — the whole test runs and rolls back inside FrappeTestCase.

Why unit-test the GL methods instead of running a full POS shift close?
The override only fires on a *consolidated* Sales Invoice (the doc a POS shift
close produces). Standing up that entire flow — opening entry, POS invoices with
redeemed points, closing entry, consolidation — for every assertion is slow and
brittle. The risk we actually care about is narrow and lives entirely in these
methods: does the redemption get booked out of Debtors, does the return reverse it
back to the same account, and do both stay strict no-ops on ordinary invoices.
Calling the methods directly tests exactly that, deterministically.

The accounts/customer are discovered from whatever company on the site already has
a full chart of accounts, so the test is not pinned to one tenant.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

REDEEM_AMOUNT = 20.0


def _find_test_company():
    """A company on the site with the accounts these tests need.

    Returns (company, debit_to, redemption_account, cost_center) or None if no
    company on the site has a full chart of accounts yet (fresh site, no company).
    """
    for company in frappe.get_all("Company", pluck="name"):
        debit_to = frappe.db.get_value(
            "Account",
            {"company": company, "account_type": "Receivable", "is_group": 0},
            "name",
        )
        redemption = frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Expense", "is_group": 0},
            "name",
        )
        cost_center = frappe.db.get_value("Company", company, "cost_center")
        if debit_to and redemption and cost_center:
            return company, debit_to, redemption, cost_center
    return None


class TestLoyaltyRedemptionGL(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        found = _find_test_company()
        if not found:
            raise unittest.SkipTest("No company with a chart of accounts on this site")
        cls.company, cls.debit_to, cls.redemption, cls.cost_center = found
        cls.customer = frappe.db.get_value("Customer", {"disabled": 0}, "name") or frappe.db.get_value(
            "Customer", {}, "name"
        )

    def _invoice(self, **fields):
        """A bare in-memory Sales Invoice with the fields get_gl_dict needs, plus overrides."""
        si = frappe.new_doc("Sales Invoice")
        si.company = self.company
        si.customer = self.customer
        si.debit_to = self.debit_to
        si.cost_center = self.cost_center
        si.posting_date = frappe.utils.nowdate()
        si.currency = frappe.db.get_value("Company", self.company, "default_currency")
        si.conversion_rate = 1
        for key, value in fields.items():
            setattr(si, key, value)
        return si

    def _redeeming_sale(self, **overrides):
        base = dict(
            is_consolidated=1,
            redeem_loyalty_points=1,
            loyalty_points=REDEEM_AMOUNT,
            loyalty_program=self._any_program(),
            loyalty_amount=REDEEM_AMOUNT,
            loyalty_redemption_account=self.redemption,
        )
        base.update(overrides)
        return self._invoice(**base)

    def _any_program(self):
        # loyalty_program only has to be truthy for the guard; the doc is never saved.
        return frappe.db.get_value("Loyalty Program", {}, "name") or "PLACEHOLDER-PROGRAM"

    # ── the override is actually wired ──────────────────────────────────────────

    def test_new_doc_uses_barakat_override(self):
        from barakat.overrides.sales_invoice import BarakatSalesInvoice

        self.assertIsInstance(frappe.new_doc("Sales Invoice"), BarakatSalesInvoice)

    # ── sale: redemption is booked out of Debtors, balanced ─────────────────────

    def test_sale_books_redemption_out_of_debtors(self):
        si = self._redeeming_sale()
        self.assertTrue(si._barakat_books_consolidated_redemption())

        gl = []
        si.make_loyalty_point_redemption_gle(gl)

        self.assertEqual(len(gl), 2, "expected exactly the two redemption lines")
        by_account = {e["account"]: e for e in gl}

        # The redeemed value is credited OUT of the customer receivable...
        debtors_line = by_account[self.debit_to]
        self.assertEqual(flt(debtors_line.get("credit")), REDEEM_AMOUNT)
        self.assertEqual(debtors_line.get("party"), self.customer)

        # ...and debited INTO the redemption account.
        redemption_line = by_account[self.redemption]
        self.assertEqual(flt(redemption_line.get("debit")), REDEEM_AMOUNT)

        # The pair is self-balancing: debits == credits.
        total_debit = sum(flt(e.get("debit")) for e in gl)
        total_credit = sum(flt(e.get("credit")) for e in gl)
        self.assertEqual(total_debit, total_credit)

    # ── guards: strict no-op on ordinary invoices ───────────────────────────────

    def test_guard_off_for_non_consolidated_invoice(self):
        si = self._redeeming_sale(is_consolidated=0)
        self.assertFalse(si._barakat_books_consolidated_redemption())

    def test_guard_off_when_no_points_redeemed(self):
        si = self._invoice(is_consolidated=1)
        self.assertFalse(si._barakat_books_consolidated_redemption())

    # ── return: the balancing write-off reverses into the redemption account ────

    def test_return_redirects_write_off_to_redemption_account(self):
        write_off_account = frappe.db.get_value(
            "Account", {"company": self.company, "root_type": "Expense", "is_group": 0}, "name"
        )
        ret = self._invoice(
            # is_pos: upstream make_write_off_gl_entry only books a write-off on a POS
            # invoice, which every consolidated POS invoice is. base_write_off_amount is
            # the company-currency figure the ledger lines are actually built from.
            is_pos=1,
            is_consolidated=1,
            is_return=1,
            return_against="ANY-ORIGINAL-SALE",
            write_off_amount=REDEEM_AMOUNT,
            base_write_off_amount=REDEEM_AMOUNT,
            write_off_account=write_off_account,
            write_off_cost_center=self.cost_center,
            loyalty_redemption_account=self.redemption,
        )

        gl = []
        ret.make_write_off_gl_entry(gl)

        accounts = [e["account"] for e in gl]
        self.assertIn(self.redemption, accounts, "write-off should be redirected to the redemption account")

        # The receivable side of the reversal is allocated against the RETURN, not the
        # original sale (which already self-settled its own redemption).
        for e in gl:
            if e["account"] == self.debit_to:
                self.assertEqual(e.get("against_voucher"), ret.name)

        # The swapped account must never be left on the document.
        self.assertEqual(ret.write_off_account, write_off_account)

    def test_plain_write_off_is_left_alone(self):
        # A return with no loyalty context must fall through to stock's own behavior.
        ret = self._invoice(is_consolidated=1, is_return=1, write_off_amount=REDEEM_AMOUNT)
        self.assertIsNone(ret._barakat_loyalty_reversal_account())


def _find_loyalty_fixture():
    """Everything the end-to-end test needs, discovered from live site data.

    Requires a Loyalty Program that actually has an expense (redemption) account —
    that program's company supplies the receivable / income / cost-center context.
    Returns a dict or None (fresh site with no configured loyalty program).
    """
    for lp in frappe.get_all(
        "Loyalty Program", filters={"expense_account": ["is", "set"]}, fields=["name", "company", "expense_account"]
    ):
        company = lp.company
        debtors = frappe.db.get_value(
            "Account", {"company": company, "account_type": "Receivable", "is_group": 0}, "name"
        )
        income = frappe.db.get_value(
            "Account", {"company": company, "root_type": "Income", "is_group": 0}, "name"
        )
        cost_center = frappe.db.get_value("Company", company, "cost_center")
        item = frappe.db.get_value("Item", {"is_sales_item": 1, "disabled": 0, "has_variants": 0}, "name")
        customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        if all([debtors, income, cost_center, item, customer]):
            return {
                "program": lp.name,
                "company": company,
                "redemption": lp.expense_account,
                "debtors": debtors,
                "income": income,
                "cost_center": cost_center,
                "item": item,
                "customer": customer,
            }
    return None


class TestLoyaltyRedemptionEndToEnd(FrappeTestCase):
    """Full submit-to-ledger test: build a POS-style consolidated invoice paid with
    points, submit it, and assert the REAL GL Entry rows the database ends up with.

    This is the heavier companion to the unit tests above. Where those check the GL
    lines our override *builds*, this proves the whole posting path: after submit the
    redeemed value is booked into the redemption account, the customer's receivable
    nets to zero, and the invoice settles as "Paid" rather than being stranded
    "Partly Paid" (the concrete bug the override exists to fix).

    Nothing is committed — FrappeTestCase rolls the transaction back after the test,
    so no invoice or ledger row survives. Only the return path is left to a manual
    reconciliation: faithfully reproducing a consolidated *credit note* requires the
    real shift-close / merge pipeline, not a hand-built document.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx = _find_loyalty_fixture()
        if not cls.fx:
            raise unittest.SkipTest("No Loyalty Program with a redemption account on this site")

    def test_sale_paid_with_points_settles_on_the_real_ledger(self):
        fx = self.fx
        si = frappe.new_doc("Sales Invoice")
        si.company = fx["company"]
        si.customer = fx["customer"]
        si.debit_to = fx["debtors"]
        si.set_posting_time = 1
        si.posting_date = frappe.utils.nowdate()
        si.append(
            "items",
            {
                "item_code": fx["item"],
                "qty": 1,
                "rate": REDEEM_AMOUNT,
                "income_account": fx["income"],
                "cost_center": fx["cost_center"],
            },
        )
        si.flags.ignore_permissions = True
        # Insert as an ordinary invoice so ERPNext computes the totals, THEN turn it into
        # the consolidated redemption invoice a shift close produces. Building it as
        # consolidated from the start skips total calculation (the merge log normally
        # fills those in) and leaves grand_total unset.
        si.insert()
        si.is_consolidated = 1
        si.redeem_loyalty_points = 1
        si.loyalty_program = fx["program"]
        si.loyalty_points = REDEEM_AMOUNT
        si.loyalty_amount = REDEEM_AMOUNT
        si.loyalty_redemption_account = fx["redemption"]
        si.loyalty_redemption_cost_center = fx["cost_center"]
        si.flags.ignore_validate_update_after_submit = True
        si.submit()

        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_no": si.name, "is_cancelled": 0},
            fields=["account", "debit", "credit"],
        )

        # The redeemed value is booked INTO the redemption account.
        redemption_debit = sum(flt(e.debit) for e in gl if e.account == fx["redemption"])
        self.assertEqual(redemption_debit, REDEEM_AMOUNT)

        # The customer's receivable nets to zero — the points settled the sale.
        debtors_net = sum(flt(e.debit) - flt(e.credit) for e in gl if e.account == fx["debtors"])
        self.assertEqual(debtors_net, 0.0)

        # And the invoice is fully settled, not stranded "Partly Paid".
        self.assertEqual(flt(si.outstanding_amount), 0.0)
        self.assertEqual(si.status, "Paid")
