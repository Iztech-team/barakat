"""Re-price loyalty earn rows that a return recomputed at the wrong tier's rate.

`barakat.overrides.loyalty.reprice_earn_at` holds the rule and the defect it answers:
erpnext rebuilds a sale's earn row on every return and re-resolves the customer's tier
**as it stands at return time**, so a customer who has climbed a tier since has the
remainder of an old sale re-priced at the new, richer rate. With tiers
`0 → factor 10` and `1000 → factor 5`, returning 400 of a 928 sale rebuilt its row as
528 / 5 = 105 where the sale had only ever granted 92 — cash back AND points up. The
override fixes new returns. This fixes the ledger already written.

Only two kinds of row are visited, and the narrowing matters because the repair is not
free to get wrong:

  * programs whose tiers do NOT all share one `collection_factor`. Where every tier
    earns at the same rate the bug cannot express itself, which is most sites, and
    they exit immediately.
  * invoices that actually carry a submitted return. Those are the only rows erpnext
    ever rebuilt; everywhere else the row still holds what the sale granted.

**The tier is reconstructed from the customer's history, not from the ledger as it
stands today.** Those are different numbers and only the first is right: today's
`purchase_amount` on an earlier sale is already net of returns taken *after* the sale
being repaired, so replaying with current figures can push a sale below a threshold it
genuinely crossed. Each invoice's contribution is therefore rebuilt as its own
`grand_total` less only those returns created *before* the sale under repair — which
is exactly what `get_loyalty_details` summed at the moment erpnext priced it.

Rows are only written when they actually differ, so a second run finds nothing and the
patch is idempotent. Every move is printed, in both directions, so there is a record of
which invoice changed and by how much.
"""

import frappe
from frappe.utils import cint, flt

# The same re-stamp the sibling loyalty patch performs, reused rather than repeated:
# re-pricing rows changes no money, but it changes balances, and a customer left on a
# tier the corrected ledger does not support keeps earning at the wrong rate.
from barakat.patches.align_loyalty_purchase_amount import _restamp_tiers


def execute():
	programs = _programs_with_more_than_one_rate()
	if not programs:
		print("[loyalty] every loyalty program earns at one rate — nothing to re-price")
		return

	repriced = 0
	touched = set()
	for program in programs:
		rules = _rules(program)
		if not rules:
			continue
		for (customer, _company), rows in _rows_by_customer(program).items():
			try:
				changed = _repair(rows, rules)
			except frappe.DoesNotExistError:
				# A ledger row whose invoice has been deleted outright. There is nothing
				# left to rebuild the history from, so the customer is left as found.
				print(f"[loyalty] skipped {customer} — an invoice behind its ledger is gone")
				continue
			if changed:
				repriced += changed
				touched.add(customer)

	if not repriced:
		print("[loyalty] no earn row was priced at the wrong tier")
		return

	print(
		f"[loyalty] re-priced {repriced} earn row(s) across {len(touched)} customer(s)"
	)
	_restamp_tiers(touched)


def _programs_with_more_than_one_rate():
	"""Programs where the tier a return picks can change the answer."""
	return [
		row.parent
		for row in frappe.db.sql(
			"""
			SELECT parent, COUNT(DISTINCT collection_factor) AS rates
			  FROM `tabLoyalty Program Collection`
			 WHERE parenttype = 'Loyalty Program'
			 GROUP BY parent
			HAVING rates > 1
			""",
			as_dict=True,
		)
	]


def _rules(program):
	return sorted(
		frappe.get_all(
			"Loyalty Program Collection",
			filters={"parent": program, "parenttype": "Loyalty Program"},
			fields=["tier_name", "min_spent", "collection_factor"],
		),
		key=lambda rule: flt(rule.min_spent),
	)


def _pick_tier(rules, spend):
	"""erpnext's own reading of the rules, in `get_loyalty_program_details_with_points`:
	walk them by ascending `min_spent` and keep the last one met, with the lowest
	always selected whether it is met or not."""
	chosen = rules[0]
	for index, rule in enumerate(rules):
		if index == 0 or flt(spend) >= flt(rule.min_spent):
			chosen = rule
		else:
			break
	return chosen


def _rows_by_customer(program):
	"""Earn rows for one program, grouped by (customer, company) in creation order.

	Company is part of the key because `make_loyalty_point_entry` resolves the tier
	through a company-scoped `get_loyalty_details`; spend under another company was
	never part of the total that priced this sale.

	One row per invoice: should a rebuilt ledger ever hold two non-negative rows for
	one invoice, only the first is the earn — the same reading `align_invoice_spend`
	makes.
	"""
	grouped = {}
	seen = set()
	for row in frappe.db.sql(
		"""
		SELECT name, customer, company, invoice_type, invoice,
		       loyalty_points, loyalty_program_tier
		  FROM `tabLoyalty Point Entry`
		 WHERE loyalty_program = %s
		   AND loyalty_points >= 0
		   AND COALESCE(invoice, '') <> ''
		 ORDER BY creation ASC
		""",
		(program,),
		as_dict=True,
	):
		key = (row.invoice_type, row.invoice)
		if key in seen:
			continue
		seen.add(key)
		grouped.setdefault((row.customer, row.company or ""), []).append(row)
	return grouped


def _facts(doctype, invoice):
	"""What the invoice was worth, when it happened, and what came back against it."""
	doc = frappe.db.get_value(
		doctype,
		invoice,
		["grand_total", "loyalty_amount", "posting_date", "creation"],
		as_dict=True,
	)
	if not doc:
		raise frappe.DoesNotExistError(f"{doctype} {invoice}")
	doc.returns = [
		(row.creation, abs(flt(row.grand_total)))
		for row in frappe.get_all(
			doctype,
			filters={"return_against": invoice, "docstatus": 1},
			fields=["creation", "grand_total"],
		)
	]
	return doc


def _spent_before(rows, facts, target):
	"""The customer's total spend at the moment `target` was rung up.

	Rebuilt from each earlier invoice's own bill less the returns that had already
	happened by then — never from today's `purchase_amount`, which has later returns
	baked into it. Both bounds are needed: `posting_date` is what
	`get_loyalty_details` filtered on, and `creation` is what had actually been
	written when the sale was priced.
	"""
	total = 0.0
	for row in rows:
		other = facts[row.name]
		if other is target:
			continue
		if other.creation >= target.creation:
			continue
		if other.posting_date > target.posting_date:
			continue
		returned_by_then = sum(
			amount for (created, amount) in other.returns if created < target.creation
		)
		total += flt(other.grand_total) - returned_by_then
	return total


def _repair(rows, rules):
	facts = {row.name: _facts(row.invoice_type, row.invoice) for row in rows}

	changed = 0
	for row in rows:
		target = facts[row.name]
		if not target.returns:
			# Never rebuilt, so it still holds what the sale granted.
			continue

		# erpnext's own expressions, with its own rounding (`cint` truncates). The
		# only thing reconstructed rather than read is which tier supplies the divisor.
		current = flt(target.grand_total) - cint(target.loyalty_amount)
		tier = _pick_tier(rules, _spent_before(rows, facts, target) + current)
		factor = flt(tier.collection_factor) or 1.0
		eligible = current - sum(amount for (_created, amount) in target.returns)
		points = cint(eligible / factor)

		updates = {}
		if cint(row.loyalty_points) != points:
			updates["loyalty_points"] = points
		if row.loyalty_program_tier != tier.tier_name:
			updates["loyalty_program_tier"] = tier.tier_name
		if not updates:
			continue

		frappe.db.set_value("Loyalty Point Entry", row.name, updates)
		changed += 1
		print(
			f"[loyalty] {row.invoice}: {cint(row.loyalty_points)} → {points} point(s) "
			f"at {tier.tier_name} (was stamped {row.loyalty_program_tier or '—'})"
		)
	return changed
