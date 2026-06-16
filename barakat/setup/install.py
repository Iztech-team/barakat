import frappe


def after_install():
	for fn in [
		_enable_negative_stock,
		_set_session_expiry,
		_set_pos_invoice_type,
		_set_commercial_rounding,
		_create_misc_item,
		_create_default_customer,
		_provision_barakat_roles,
	]:
		try:
			fn()
		except Exception as e:
			frappe.log_error(f"barakat after_install: {fn.__name__} failed: {e}", "Install")
	frappe.db.commit()


def _enable_negative_stock():
	frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)


def _set_pos_invoice_type():
	frappe.db.set_single_value("POS Settings", "invoice_type", "POS Invoice")


def _set_commercial_rounding():
	frappe.db.set_single_value("System Settings", "rounding_method", "Commercial Rounding")
	frappe.db.set_default("rounding_method", "Commercial Rounding")
	frappe.clear_document_cache("System Settings", "System Settings")


def _set_session_expiry():
	frappe.db.set_single_value("System Settings", "session_expiry", "8760:00")


def _create_misc_item():
	if frappe.db.exists("Item", "MISC"):
		return
	if not frappe.db.exists("Item Group", "Miscellaneous"):
		parent = "All Item Groups" if frappe.db.exists("Item Group", "All Item Groups") else ""
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": "Miscellaneous",
				"is_group": 0,
				"parent_item_group": parent,
			}
		).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": "MISC",
			"item_name": "Miscellaneous",
			"item_group": "Miscellaneous",
			"is_stock_item": 0,
			"include_item_in_manufacturing": 0,
			"is_sales_item": 1,
			"is_purchase_item": 0,
			"description": "Generic line for ad-hoc cashier items without a catalog entry.",
		}
	).insert(ignore_permissions=True, ignore_mandatory=True)


def _create_default_customer():
	if frappe.db.exists("Customer", "Default Customer"):
		return
	customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or ""
	territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or ""
	frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": "Default Customer",
			"customer_group": customer_group,
			"territory": territory,
		}
	).insert(ignore_permissions=True, ignore_mandatory=True)


BARAKAT_ROLES = [
	"Branch Supervisor",
	"Cashier",
	"Accountant",
	"Inventory Keeper",
	"HR",
]


def _provision_barakat_roles():
	for role_name in BARAKAT_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)
