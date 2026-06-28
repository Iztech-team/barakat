"""Make Sales Invoice `custom_external_id` non-unique.

`custom_external_id` is the dedupe key the POS app stamps on the documents it
pushes (POS Invoice, Journal Entry) so the same order/movement never syncs
twice. Those should stay unique.

The consolidated **Sales Invoice** is created internally by ERPNext at shift
close, and its merge logic copies `custom_external_id` from the source POS
Invoice onto it. Marking that copied value `unique` on Sales Invoice breaks the
reopen flow: when a shift is reopened (the POS Closing Entry is cancelled), the
consolidated Sales Invoice is cancelled but NOT deleted, so its external_id row
still occupies the unique value. Re-closing re-consolidates and tries to stamp
the same external_id on a new Sales Invoice -> "External Id must be unique" ->
the shift can never be closed again.

The app never reads Sales Invoice.custom_external_id, so uniqueness there serves
no purpose. Drop it (field flag + the DB unique index).
"""

import frappe


def execute():
	# 1) Make the Custom Field non-unique so future migrations don't re-add it.
	if frappe.db.exists("Custom Field", "Sales Invoice-custom_external_id"):
		frappe.db.set_value(
			"Custom Field", "Sales Invoice-custom_external_id", "unique", 0
		)

	# 2) Drop any existing unique index on tabSales Invoice.custom_external_id.
	index_names = frappe.db.sql(
		"""
		SELECT DISTINCT INDEX_NAME
		FROM information_schema.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		  AND TABLE_NAME = 'tabSales Invoice'
		  AND COLUMN_NAME = 'custom_external_id'
		  AND NON_UNIQUE = 0
		"""
	)
	for (index_name,) in index_names:
		frappe.db.sql_ddl(
			f"ALTER TABLE `tabSales Invoice` DROP INDEX `{index_name}`"
		)

	frappe.db.commit()
