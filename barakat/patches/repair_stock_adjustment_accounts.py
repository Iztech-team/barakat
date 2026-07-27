"""Undo the damage `_fix_stock_adjustment_accounts` did to Stock Adjustment accounts.

That helper wrote `root_type=Asset, report_type=Balance Sheet` onto every account of
type "Stock Adjustment" with `frappe.db.set_value`, which skips `Account.validate`.
The result was an account whose root disagreed with its own parent: a leaf under
`Stock Expenses` (Expense) claiming to be an Asset. ERPNext would never have allowed
it through the ORM.

The repair is deliberately NOT another forced write. `Account.validate()` calls
`set_root_and_report_type()`, which re-derives both fields from the parent account,
so simply re-saving the doc restores the correct values — and would refuse if the
tree were genuinely inconsistent. Fixing a bad `db.set_value` with another
`db.set_value` is how the original bug was written.

BOOKS IMPACT — read before running on a live site. GL Entry rows do not store the
account's type; reports resolve it from the Account at read time. So repairing an
account that already has entries moves those entries from the Balance Sheet into the
Profit & Loss, retroactively. That is the correct place for them (stock adjustments
are damage and count losses, i.e. expenses), but it does change previously reported
profit for the periods involved. Approved for this repo on 2026-07-27.
"""

import frappe


def execute():
	accounts = frappe.get_all(
		"Account",
		filters={"account_type": "Stock Adjustment"},
		fields=["name", "root_type", "report_type", "parent_account"],
	)

	for acc in accounts:
		# A root account has no parent to re-derive from; leave it alone rather than
		# guess. None exist in practice — Stock Adjustment is always a leaf — but a
		# malformed chart should not take the whole migrate down.
		if not acc.parent_account:
			continue

		parent_root = frappe.db.get_value("Account", acc.parent_account, "root_type")
		if parent_root and acc.root_type == parent_root:
			continue  # already consistent with its parent — nothing to repair

		try:
			doc = frappe.get_doc("Account", acc.name)
			doc.save(ignore_permissions=True)
			frappe.logger().info(
				f"repair_stock_adjustment_accounts: {acc.name} "
				f"{acc.root_type}/{acc.report_type} -> {doc.root_type}/{doc.report_type}"
			)
		except Exception as e:
			# One malformed account must not abort the migrate for the whole site.
			frappe.log_error(
				f"repair_stock_adjustment_accounts: {acc.name} failed: {e}",
				"Stock Adjustment repair",
			)
