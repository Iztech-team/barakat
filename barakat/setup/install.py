import frappe


def after_install():
	for fn in [
		_enable_negative_stock,
		_set_session_expiry,
		_set_pos_invoice_type,
		_set_commercial_rounding,
		_create_default_customer,
		_provision_barakat_roles,
	]:
		try:
			fn()
		except Exception as e:
			frappe.log_error(f"barakat after_install: {fn.__name__} failed: {e}", "Install")
	frappe.db.commit()


def after_setup_wizard(args=None):
	for fn in [
		_create_misc_item,
		_fix_stock_adjustment_accounts,
	]:
		try:
			fn()
		except Exception as e:
			frappe.log_error(f"barakat after_setup_wizard: {fn.__name__} failed: {e}", "Install")
	frappe.db.commit()


def after_migrate():
	# Re-apply post-setup fixes the setup_wizard_complete hook may have missed
	# (sites restored, or provisioned via API). Each fixer is idempotent and
	# self-guarding, so this is safe to run after every migrate.
	for fn in [_create_misc_item, _fix_stock_adjustment_accounts]:
		try:
			fn()
		except Exception as e:
			frappe.log_error(f"barakat after_migrate: {fn.__name__} failed: {e}", "Install")
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


def _fix_stock_adjustment_accounts():
	# On a brand-new company the Stock Adjustment account can land under a P&L
	# (Expense) root. That blocks Bin / Stock Ledger creation when items are added,
	# so a fresh site with no demo data can't take products. Force it to an
	# Asset / Balance Sheet account so items create cleanly. Idempotent — the loop
	# is empty until a company (chart of accounts) exists.
	for acc in frappe.get_all(
		"Account",
		filters={"account_type": "Stock Adjustment"},
		fields=["name", "root_type", "report_type"],
	):
		if acc.root_type != "Asset" or acc.report_type != "Balance Sheet":
			frappe.db.set_value(
				"Account",
				acc.name,
				{"root_type": "Asset", "report_type": "Balance Sheet"},
			)


def _create_misc_item():
	if frappe.db.exists("Item", "MISC"):
		return
	if not frappe.db.exists("Item Group", "All Item Groups"):
		return  # site not set up yet — item groups come from erpnext/the wizard
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
