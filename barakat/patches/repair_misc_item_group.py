import frappe


def execute():
	_repair_miscellaneous_item_group()
	_ensure_misc_item()


def _repair_miscellaneous_item_group():
	if not frappe.db.exists("Item Group", "All Item Groups"):
		return

	if not frappe.db.exists("Item Group", "Miscellaneous"):
		return

	current_parent = frappe.db.get_value("Item Group", "Miscellaneous", "parent_item_group")
	if current_parent:
		return

	doc = frappe.get_doc("Item Group", "Miscellaneous")
	doc.parent_item_group = "All Item Groups"
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _ensure_misc_item():
	if not frappe.db.exists("Item Group", "All Item Groups"):
		return

	if frappe.db.exists("Item", "MISC"):
		return

	if not frappe.db.exists("Item Group", "Miscellaneous"):
		frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": "Miscellaneous",
			"is_group": 0,
			"parent_item_group": "All Item Groups",
		}).insert(ignore_permissions=True)

	frappe.get_doc({
		"doctype": "Item",
		"item_code": "MISC",
		"item_name": "Miscellaneous",
		"item_group": "Miscellaneous",
		"is_stock_item": 0,
		"include_item_in_manufacturing": 0,
		"is_sales_item": 1,
		"is_purchase_item": 0,
		"description": "Generic line for ad-hoc cashier items without a catalog entry.",
	}).insert(ignore_permissions=True, ignore_mandatory=True)
