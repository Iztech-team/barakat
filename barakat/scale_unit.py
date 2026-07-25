"""Company-side strictness for the scale feature: clearing the balance UOM
force-disables every branch row of that company (scoped by custom_company)."""

import frappe


def company_on_update(doc, method=None):
    if doc.get("custom_scale_uom"):
        return
    prev = doc.get_doc_before_save()
    if prev is None or not prev.get("custom_scale_uom"):
        return  # not a set->empty transition
    if not frappe.db.table_exists("POS Scale Settings"):
        return
    frappe.db.sql(
        """update `tabPOS Scale Settings`
           set scale_barcode_enabled=0, has_balances=0
           where custom_company=%s""",
        doc.name,
    )
