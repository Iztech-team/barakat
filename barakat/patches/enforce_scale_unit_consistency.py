import frappe


def execute():
    if not frappe.db.table_exists("POS Scale Settings"):
        return  # fresh site: table syncs after patches; nothing to fix
    for company in frappe.get_all("Company", pluck="name"):
        if not frappe.db.get_value("Company", company, "custom_scale_uom"):
            frappe.db.sql(
                """update `tabPOS Scale Settings`
                   set scale_barcode_enabled=0, has_balances=0
                   where custom_company=%s""",
                company,
            )
    frappe.db.commit()
