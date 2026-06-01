import frappe

FILTERS = {
	"POS Profile-custom_cash_account": {
		"link_filters": '[["account_type", "=", "Cash"], ["company", "=", "eval:doc.company"]]',
	},
	"POS Profile-custom_salary_advance_account": {
		"link_filters": '[["account_type", "=", "Receivable"], ["company", "=", "eval:doc.company"]]',
		"description": "Must be a Receivable type account so ERPNext can track the balance per employee.",
	},
	"POS Profile-custom_expense_account": {
		"link_filters": '[["root_type", "=", "Expense"], ["company", "=", "eval:doc.company"]]',
		"description": "Used for Maintenance, Petty Cash, and Other cash out movements.",
	},
	"POS Profile-custom_owner_deposit_account": {
		"link_filters": '[["root_type", "in", ["Equity", "Liability"]], ["company", "=", "eval:doc.company"]]',
		"description": "Used when the owner adds money to the drawer. Typically an Equity or Liability account.",
	},
	"POS Profile-custom_bank_account": {
		"link_filters": '[["account_type", "=", "Bank"], ["company", "=", "eval:doc.company"]]',
		"description": "Used when cash is deposited from the drawer to the bank.",
	},
}


def execute():
	for name, values in FILTERS.items():
		if not frappe.db.exists("Custom Field", name):
			continue
		frappe.db.set_value("Custom Field", name, values)
	frappe.db.commit()
