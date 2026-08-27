"""Make a customer's total spend equal what their invoices came to — exactly once.

`get_loyalty_details` (loyalty_program.py) answers "total spent" by summing
`purchase_amount` over every Loyalty Point Entry the customer has, with no filter on
sign. ERPNext then writes two rows for a sale paid partly with points, and neither
one means what that sum needs it to mean:

  earn row       `purchase_amount: grand_total - loyalty_amount`  — the ELIGIBLE
                 amount, i.e. only the part of the bill that was not paid with points
  redemption row `purchase_amount: self.grand_total`              — the WHOLE bill,
                 again, and once per earn entry the redemption eats through

So a ₪23.20 sale settled with ₪13.20 cash and ₪10 of points is counted as ₪36.40.
Reported from the floor against ACC-PSINV-2026-00575: three invoices totalling
₪139.20 were shown as ₪152.40, and the gap grew with every further redemption. On
qa-test, ACC-PSINV-2026-00553 had redeemed across ten earn entries and so carried ten
redemption rows of ₪1,044 each — ₪10,440 of spend for one ₪1,044 sale.

That total is not cosmetic. It picks the loyalty tier (`set_loyalty_program_tier`
writes it onto the Customer) and the tier supplies `collection_factor` — how much
spend buys a point. An inflated total promotes customers early and then earns them
points faster than they ever paid for.

**The rule this module enforces: the bill is recorded once, on the earn row.**

  earn row        the invoice's `grand_total`, less anything returned against it
  redemption rows nothing — they record points moving, not money

Summed, that is what the customer's invoices came to, which is what "total spend"
says on the screen showing it. The alternative reading — count only the cash, leaving
the ₪10 of points out — was rejected: it sits next to the invoice list in the POS and
in the Admin Panel, and a total that disagrees with the invoices beside it is the bug
being fixed, not a different definition of it.

Points EARNED are untouched. `make_loyalty_point_entry` derives those from
`eligible_amount` in a local variable, never re-reading `purchase_amount`, so paying
with points still earns on the cash part only — ₪100 settled with ₪10 of points earns
on ₪90, exactly as before.

Nothing else reads the field: across erpnext the only reader of Loyalty Point Entry's
`purchase_amount` is that one Sum (every other `purchase_amount` belongs to Asset).

Shared by `BarakatSalesInvoice`, `BarakatPOSInvoice` and
`barakat.patches.align_loyalty_purchase_amount`, which applies the same rule to rows
written before this shipped.
"""

import frappe
from frappe.utils import cint, flt

#: Currency precision the comparison is made at. A row is only rewritten when it
#: differs in money, so a re-run — a re-submit, or the patch passing over an invoice
#: already handled — is a no-op rather than a churn of `modified`.
PRECISION = 2


def align_loyalty_spend(doc):
	"""Align the rows of a just-submitted invoice, and re-stamp the tier if they moved.

	A return is submitted against the ORIGINAL invoice's ledger — erpnext deletes and
	rebuilds the original's earn entry rather than writing rows of its own — so the
	original is aligned too, with the return already netted out of its `grand_total`.

	The re-stamp matters as much as the rows. `make_loyalty_point_entry` stamps the
	tier from the ledger as it stood a moment earlier, mid-submit, before these
	corrections; leaving that in place would keep the customer on a tier the corrected
	ledger does not support until their next sale happened to re-stamp it.
	"""
	targets = [(doc.doctype, doc.name)]
	if cint(doc.get("is_return")) and doc.get("return_against"):
		targets.append((doc.doctype, doc.return_against))

	changed = 0
	for doctype, invoice in targets:
		changed += align_invoice_spend(doctype, invoice)

	if changed and doc.get("loyalty_program"):
		doc.set_loyalty_program_tier()
	return changed


def align_invoice_spend(doctype, invoice):
	"""Rewrite one invoice's ledger rows to the rule above; return how many moved.

	Bumping `modified` on the rows it changes is deliberate: the POS pulls this ledger
	with a keyset cursor over `(modified, name)`, so a corrected row reaches the tills
	on their next sync instead of serving a stale total from the local cache forever.
	"""
	rows = frappe.get_all(
		"Loyalty Point Entry",
		filters={"invoice_type": doctype, "invoice": invoice},
		fields=["name", "loyalty_points", "purchase_amount"],
		order_by="creation asc",
	)
	if not rows:
		return 0

	spend = invoice_spend(doctype, invoice)
	changed = 0
	billed = False
	for row in rows:
		if flt(row.loyalty_points) < 0:
			# A redemption row records points leaving, not money arriving.
			target = 0.0
		elif not billed:
			# The one earn row carries the whole bill. There is normally exactly one;
			# should a rebuilt ledger ever hold two, only the first is the invoice —
			# giving the bill to both would re-create the double count from the other side.
			billed = True
			target = spend
		else:
			target = 0.0

		if flt(row.purchase_amount, PRECISION) == flt(target, PRECISION):
			continue
		frappe.db.set_value("Loyalty Point Entry", row.name, "purchase_amount", target)
		changed += 1
	return changed


def invoice_spend(doctype, invoice):
	"""What this invoice is worth to the customer's total: the bill, less returns.

	`grand_total` is after tax, which is the figure every other loyalty number in
	erpnext is built on. Returns are netted the way `make_loyalty_point_entry` nets
	them, through the invoice's own `get_returned_amount`, so a refunded sale stops
	counting toward the tier by the amount it gave back — and the query behind that
	method keys off `self.doctype`, so it finds POS Invoice returns for a POS Invoice.

	Computed from the invoice rather than by adjusting whatever is already on the row,
	so running this twice cannot drift.
	"""
	doc = frappe.get_doc(doctype, invoice)
	return flt(doc.grand_total) - flt(doc.get_returned_amount())


def release_redemptions_against(doctype, invoice):
	"""Detach redemptions hanging off an invoice's ledger rows; return how many moved.

	Called just before erpnext deletes those rows, which it does on every return and
	every cancel: `POSInvoice.on_submit` runs
	`delete_loyalty_point_entry()` + `make_loyalty_point_entry()` on the ORIGINAL sale
	so the earn is recomputed with the returned amount netted out.

	`delete_loyalty_point_entry` refuses when a redemption row points at the row it is
	about to delete, and tells the cashier to cancel the invoice that SPENT the points
	first. On a till that is not an instruction anyone can follow — the customer who
	spent them has walked out with the goods. Reported as 0001-610: a customer earned
	across three sales, spent two points on a fourth, then returned the first. Only the
	first fails, because `apply_loyalty_points` allocates redemptions FIFO (oldest
	expiry first, `sales_invoice.py`), so the oldest sale is the only one carrying a
	`redeem_against` link. Worse than the message: the POS pays the cash out first and
	pushes afterwards, so a Frappe ValidationError (417 → non-retriable) left the till
	short with no return recorded anywhere.

	Detaching is safe because `redeem_against` guards nothing. A balance is a plain
	`SUM(loyalty_points)` (`get_loyalty_details`), and the redemption cap is checked
	against that TOTAL — "You don't have enough Loyalty Points to redeem",
	`loyalty_program.validate_loyalty_points` — never per earn row. The link is used
	only by `get_redemption_details`, to spread a new redemption across earn rows. A
	detached row simply stops reducing any one row's share; it still reduces the
	balance, which is the number that decides what the customer may spend.

	It also closes a live landmine rather than stepping around one.
	`apply_loyalty_points` computes `available_points = row.loyalty_points -
	redeemed_against_it` and, when that is NEGATIVE, writes `-1 * available_points` —
	a POSITIVE entry, minting points from nothing. An earn row falling below what is
	already redeemed against it is exactly what erpnext's own delete-and-recreate does
	on a PARTIAL return; today only the throw above stops it. Detaching first means no
	row is ever left over-attributed, so that path stays unreachable.

	What it does NOT do is decide who absorbs an over-spend: the points come off the
	balance in full, and a balance that lands below zero is left below zero. The
	customer cannot redeem again until they earn back past it, which is what stops the
	cycle repeating. Judging the one-off case belongs at the till, in front of a
	person, not here.
	"""
	rows = frappe.get_all(
		"Loyalty Point Entry",
		filters={"invoice_type": doctype, "invoice": invoice},
		pluck="name",
	)
	if not rows:
		return 0

	dependents = frappe.get_all(
		"Loyalty Point Entry",
		filters={"redeem_against": ["in", rows]},
		pluck="name",
	)
	for name in dependents:
		# `redeem_against` is a nullable Link, and a Loyalty Point Entry is not
		# submittable, so this is an ordinary field write — no cancel/amend dance.
		frappe.db.set_value("Loyalty Point Entry", name, "redeem_against", None)
	return len(dependents)
