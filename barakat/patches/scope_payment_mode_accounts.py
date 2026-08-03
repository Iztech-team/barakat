"""Remove other shops' rows from a payment mode that belongs to one shop.

The cleanup half of ticket 0001-595. `BarakatCompany.set_mode_of_payment_account`
(barakat 4.0.1) stops NEW rows being written; this removes the ones already there.
It must run after that fix, or the next company save puts them straight back.

## What went wrong

ERPNext's `Company.on_update` appended this company's cash account to whichever
Cash-type Mode of Payment the database returned first — no company filter. With a
mode per shop (`custom_company`), shops collected each other's accounts. A mode
naming a second company is then unreadable to EVERY persona, because a staff User
Permission pins them to one company and Frappe's document check walks child rows
too, so the shop could not record a supplier payment at all.

## The safety rule

A foreign row is only deleted when that foreign company still has a payment-mode
row **on a mode of its own**. Otherwise the row is the only cash mapping that
company has, and removing it would take away something it might still be using —
so it is reported and left alone, for a human to place deliberately.

That rule matters more than it looks. The proxy's `backfillModeCompany` tags an
untagged mode with the FIRST company in its accounts table, so a shared mode that
gathered four shops can suddenly become "owned" by one of them — at which point
the other three rows look foreign to this patch. Without the rule, a later run
would quietly strip three shops of their cash account.

## What this deliberately does NOT touch

A mode with no `custom_company` (stock ERPNext's global "Cash") is shared by
design, so its multi-company rows are not pollution and are not ours to delete —
even though the same read block applies to it. Those are reported instead: making
them per-shop is a product decision, not a data repair.

Idempotent: a second run finds nothing to do.

`decide` is deliberately Frappe-free — the risk here is deleting a row that should
have been kept, which is a decision, not a query — so it is imported by a plain
unittest that runs without a bench. `frappe` is imported inside the two functions
that actually touch the site.
"""

COMPANY_FIELD = "custom_company"


def decide(owner_by_mode, companies_by_mode):
	"""The whole decision, as pure Python — no Frappe, so it is unit-testable.

	`owner_by_mode`     {mode: company it belongs to, "" when untagged}
	`companies_by_mode` {mode: [company on each accounts row]}

	Returns (to_delete, kept_unsafe, shared_modes):
	  to_delete    [(mode, company)] another shop's row, safe to drop
	  kept_unsafe  [(mode, company)] foreign row that is that company's ONLY
	               row on a mode of its own — left alone and reported
	  shared_modes [(mode, [companies])] untagged mode naming more than one company
	"""
	# Which companies already hold a row on a mode tagged as theirs? Those are the
	# only ones whose foreign rows we may remove — see the safety rule above.
	safe_elsewhere = {
		company
		for mode, owner in owner_by_mode.items()
		if owner
		for company in companies_by_mode.get(mode, [])
		if company == owner
	}

	to_delete, kept_unsafe, shared_modes = [], [], []
	for mode, owner in owner_by_mode.items():
		companies = companies_by_mode.get(mode, [])
		if not owner:
			distinct = sorted({c for c in companies if c})
			if len(distinct) > 1:
				shared_modes.append((mode, distinct))
			continue
		for company in companies:
			if not company or company == owner:
				continue
			target = to_delete if company in safe_elsewhere else kept_unsafe
			target.append((mode, company))
	return to_delete, kept_unsafe, shared_modes


def plan():
	"""Read the site, then `decide`. Changes nothing.

	Split from `execute` so the same decision can be inspected on a live site
	before anything is deleted.
	"""
	import frappe

	owner_by_mode = {
		m.name: (m.get(COMPANY_FIELD) or "").strip()
		for m in frappe.get_all("Mode of Payment", fields=["name", COMPANY_FIELD])
	}
	companies_by_mode = {}
	for r in frappe.get_all(
		"Mode of Payment Account", fields=["parent", "company"], order_by="parent, idx"
	):
		companies_by_mode.setdefault(r.parent, []).append(r.company)
	return decide(owner_by_mode, companies_by_mode)


def execute():
	import frappe

	to_delete, kept_unsafe, shared_modes = plan()

	if not to_delete:
		print("[barakat] payment modes: no foreign company rows to remove")
	else:
		drop = {}
		for mode, company in to_delete:
			drop.setdefault(mode, set()).add(company)
		for mode, companies in drop.items():
			# Saved through the document, not a raw row delete, so `idx` is renumbered
			# rather than left with the gaps the appends created (1, 6, 7, 8).
			doc = frappe.get_doc("Mode of Payment", mode)
			doc.set("accounts", [r for r in doc.accounts if r.company not in companies])
			doc.save(ignore_permissions=True)
			print(f"[barakat] {mode}: removed {sorted(companies)}")
		print(f"[barakat] payment modes: removed {len(to_delete)} foreign row(s)")

	for mode, company in kept_unsafe:
		print(
			f"[barakat] KEPT {mode}: {company!r} has no payment mode of its own — "
			f"left in place; give that company its own mode, then re-run this patch."
		)
	for mode, companies in shared_modes:
		print(
			f"[barakat] SHARED {mode} names {len(companies)} companies {companies} — "
			f"not touched (no custom_company, so shared by design), but staff of those "
			f"companies cannot read it. Needs a per-company mode to replace it."
		)
