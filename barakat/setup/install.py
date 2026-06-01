import frappe


def after_install():
	for fn in [
		_enable_negative_stock,
		_set_session_expiry,
		_set_pos_invoice_type,
		_create_misc_item,
		_create_default_customer,
		_create_device_custom_fields,
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


def _create_device_custom_fields():
	"""Create custom fields for the device-profile mapping and cash movement system."""
	fields = [
		# Branch: table of POS Profiles
		{
			"dt": "Branch",
			"fieldname": "custom_pos_profiles",
			"label": "POS Profiles",
			"fieldtype": "Table",
			"options": "Branch POS Profile",
			"insert_after": "custom_pos_company",
		},
		# POS Profile: section break grouping all Barakat fields
		{
			"dt": "POS Profile",
			"fieldname": "custom_barakat_section",
			"label": "Barakat Settings",
			"fieldtype": "Section Break",
			"insert_after": "allow_partial_payment",
		},
		# POS Profile: which device is linked to this profile
		{
			"dt": "POS Profile",
			"fieldname": "custom_device",
			"label": "Linked Device",
			"fieldtype": "Link",
			"options": "Device",
			"insert_after": "custom_barakat_section",
			"read_only": 0,
			"in_list_view": 1,
		},
		# POS Profile: which branch this profile belongs to (read-only, set by Branch validate)
		{
			"dt": "POS Profile",
			"fieldname": "custom_branch",
			"label": "Branch",
			"fieldtype": "Link",
			"options": "Branch",
			"insert_after": "custom_device",
			"read_only": 1,
			"in_list_view": 1,
		},
		# POS Profile: the physical cash drawer account for this device
		{
			"dt": "POS Profile",
			"fieldname": "custom_cash_account",
			"label": "Cash Drawer Account",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": "custom_branch",
			"link_filters": '[["account_type", "=", "Cash"], ["company", "=", "eval:doc.company"]]',
		},
		# POS Profile: account used for the other side of salary advance movements.
		# Must be Receivable type so ERPNext can tag the employee as a party on the JE.
		{
			"dt": "POS Profile",
			"fieldname": "custom_salary_advance_account",
			"label": "Salary Advance Account",
			"description": "Must be a Receivable type account so ERPNext can track the balance per employee.",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": "custom_cash_account",
			"link_filters": '[["account_type", "=", "Receivable"], ["company", "=", "eval:doc.company"]]',
		},
		# POS Profile: account used for the other side of expense movements (maintenance, petty cash, other)
		{
			"dt": "POS Profile",
			"fieldname": "custom_expense_account",
			"label": "Expense Account",
			"description": "Used for Maintenance, Petty Cash, and Other cash out movements.",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": "custom_salary_advance_account",
			"link_filters": '[["root_type", "=", "Expense"], ["company", "=", "eval:doc.company"]]',
		},
		# POS Profile: account used for the other side of owner deposit movements
		{
			"dt": "POS Profile",
			"fieldname": "custom_owner_deposit_account",
			"label": "Owner Deposit Account",
			"description": "Used when the owner adds money to the drawer. Typically an Equity or Liability account.",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": "custom_expense_account",
			"link_filters": '[["root_type", "in", ["Equity", "Liability"]], ["company", "=", "eval:doc.company"]]',
		},
		# POS Profile: bank account used for the other side of bank deposit movements
		{
			"dt": "POS Profile",
			"fieldname": "custom_bank_account",
			"label": "Bank Account",
			"description": "Used when cash is deposited from the drawer to the bank.",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": "custom_owner_deposit_account",
			"link_filters": '[["account_type", "=", "Bank"], ["company", "=", "eval:doc.company"]]',
		},
	]

	for f in fields:
		if frappe.db.exists("Custom Field", {"dt": f["dt"], "fieldname": f["fieldname"]}):
			continue
		frappe.get_doc({"doctype": "Custom Field", **f}).insert(ignore_permissions=True)
