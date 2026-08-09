"""Re-record the money on loyalty ledger rows written before the override shipped,
and re-stamp the tiers the old figures inflated.

`barakat.overrides.loyalty` holds the rule and the defect it answers: erpnext records
a sale paid partly with points twice — the whole bill on the redemption row, the cash
part on the earn row — and `get_loyalty_details` sums them both. The override fixes
new invoices. This fixes the ledger already written.

Only invoices carrying a redemption row are visited. Everywhere else the earn row
already holds the bill, because with no points redeemed `grand_total - loyalty_amount`
IS the bill — so there is nothing to correct and no reason to walk the whole ledger.

**Some customers drop a tier**, and that is the point: the tier feeds
`collection_factor`, so a customer left on a level they reached only through
double-counted spend keeps earning points faster than they paid for. Every move is
printed by name, in both directions, so there is a record of who changed and to what.

The tier is recomputed through erpnext's own `get_loyalty_program_details_with_points`
rather than a local reimplementation, so it cannot drift from what the next invoice
will stamp. It is read against the program and company on the customer's most recent
entry, mirroring the fact that the field is singular and the last invoice to touch a
customer is what set it.

Idempotent: `align_invoice_spend` only writes rows that differ in money, and a second
run finds none.
"""

import frappe

from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
	get_loyalty_program_details_with_points,
)

from barakat.overrides.loyalty import align_invoice_spend


def execute():
	invoices = _invoices_with_redemptions()
	if not invoices:
		print("[loyalty] no invoice redeemed points — nothing to re-record")
		return

	rows = 0
	touched = set()
	for invoice in invoices:
		try:
			changed = align_invoice_spend(invoice.invoice_type, invoice.invoice)
		except frappe.DoesNotExistError:
			# A ledger row whose invoice has been deleted outright. Nothing to read the
			# bill from, so it is left exactly as found and reported.
			print(f"[loyalty] skipped {invoice.invoice} — the invoice is gone")
			continue
		if changed:
			rows += changed
			touched.add(invoice.customer)

	print(
		f"[loyalty] re-recorded {rows} row(s) across {len(touched)} customer(s), "
		f"from {len(invoices)} invoice(s) that redeemed points"
	)
	_restamp_tiers(touched)


def _invoices_with_redemptions():
	"""Every invoice that has a redemption row, with the customer it belongs to."""
	return frappe.db.sql(
		"""
		SELECT DISTINCT invoice_type, invoice, customer
		  FROM `tabLoyalty Point Entry`
		 WHERE loyalty_points < 0
		   AND COALESCE(invoice, '') <> ''
		""",
		as_dict=True,
	)


def _restamp_tiers(customers):
	"""Recompute each customer's tier from the corrected ledger and report the moves.

	A single unstampable customer — a deleted program, a broken collection rule — must
	not abort a migration that has already corrected the ledger, so each is isolated
	and logged.
	"""
	moved = 0
	for customer in sorted(customers):
		latest = frappe.get_all(
			"Loyalty Point Entry",
			filters={"customer": customer},
			fields=["loyalty_program", "company"],
			order_by="posting_date desc, creation desc",
			limit=1,
		)
		if not latest:
			continue

		before = frappe.db.get_value("Customer", customer, "loyalty_program_tier")
		try:
			details = get_loyalty_program_details_with_points(
				customer,
				loyalty_program=latest[0].loyalty_program,
				company=latest[0].company,
				include_expired_entry=True,
				silent=True,
			)
		except Exception:
			frappe.log_error(
				title="align_loyalty_purchase_amount",
				message=f"could not re-stamp the tier for {customer}\n{frappe.get_traceback()}",
			)
			print(f"[loyalty] could not re-stamp {customer} — see the error log")
			continue

		after = details.get("tier_name")
		if after == before:
			continue
		frappe.db.set_value("Customer", customer, "loyalty_program_tier", after)
		print(f"[loyalty] tier {before or '—'} → {after or '—'} for {customer}")
		moved += 1

	if not moved:
		print("[loyalty] no customer changed tier")
