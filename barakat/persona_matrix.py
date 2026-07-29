"""The admin-panel persona matrix, transcribed for the ERPNext side.

`proxy-barakat/src/modules/roles/catalog.ts` is the source of truth. This module is
its Python twin, kept honest by `persona_matrix.json`, a byte-identical snapshot
committed to BOTH repos with a test on each side. A hand-copy without that guard is
what broke this file once before: a bundle snapshotted from pos2 named two roles
local to that site, and staff creation failed outright on every other site.

Frappe-free on purpose: imported by pure unittests and by hooks.py at load time.
"""

MODULE_KEYS = (
	"dashboard",
	"pos",
	"products",
	"inventory",
	"warehouses",
	"branches",
	"staff",
	"roles",
	"attendance",
	"salary",
	"finance",
	"reports",
	"settings",
	"accounting",
	"customers",
	"suppliers",
	"reports.sales",
	"reports.products",
	"reports.inventory",
	"reports.staff",
	"reports.pos",
	"reports.salary",
	"reports.suppliers",
)

# Derived from the proxy's service code (every erp.list/get/create/update/delete call
# in src/modules/*) and the till's /api/resource/* paths — not guessed. See the design
# doc's "Proving nothing is missing / A".
#
# `dashboard`, `roles` and `reports` map to no ERPNext doctype the persona reads
# directly: `dashboard` is an AP landing page, `reports` is the parent gate whose
# sub-keys carry the real doctypes, and `roles` is served from the proxy's own
# code-defined catalog rather than the Role table. They generate no role.
MODULE_DOCTYPES = {
	"dashboard": (),
	"pos": (
		"POS Invoice",
		"POS Opening Entry",
		"POS Closing Entry",
		"POS Profile",
		"POS Employee Branch",
		"Device",
	),
	"products": (
		"Item",
		"Item Group",
		"Item Price",
		"Product Bundle",
		"UOM",
		"Price List",
		"Bin",
		"Pricing Rule",
	),
	"inventory": ("Stock Entry", "Stock Reconciliation", "Stock Ledger Entry", "Bin"),
	"warehouses": ("Warehouse",),
	"branches": ("Branch",),
	"staff": ("Employee", "Designation", "Holiday List", "Holiday List Assignment", "User"),
	"roles": (),
	"attendance": ("Attendance",),
	"salary": (
		"Salary Slip",
		"Salary Structure",
		"Salary Component",
		"Salary Structure Assignment",
		"Payroll Settings",
	),
	"finance": ("GL Entry", "Journal Entry", "Payment Entry", "Fiscal Year"),
	"reports": (),
	"settings": ("Company", "Global Defaults", "System Settings", "POS Scale Settings"),
	"accounting": (
		"Account",
		"Mode of Payment",
		"Sales Taxes and Charges Template",
		"Currency",
		"Currency Exchange",
	),
	"customers": (
		"Customer",
		"Customer Group",
		"Contact",
		"Loyalty Program",
		"Loyalty Point Entry",
		"Territory",
	),
	"suppliers": ("Supplier", "Supplier Group", "Purchase Invoice"),
	"reports.sales": ("Sales Invoice", "POS Invoice"),
	"reports.products": ("Item", "Bin"),
	"reports.inventory": ("Bin", "Warehouse", "Stock Ledger Entry"),
	"reports.staff": ("Employee", "Attendance"),
	"reports.pos": ("POS Invoice", "POS Closing Entry", "Branch"),
	"reports.salary": ("Salary Slip",),
	"reports.suppliers": ("GL Entry", "Supplier"),
}

# The desktop till pulls these under a Manager / Branch Supervisor device session.
# ADDITIVE to the matrix and READ-ONLY: Branch Supervisor is `settings: none`, so a
# strict mirror of the matrix would break its tills — silently, because every till
# pull is wrapped in try/catch and falls back to defaults. Measured 2026-07-28: a
# Branch Supervisor till rounded differently from a Manager till on the same shop.
#
# Granted through `Barakat POS Operator`, which only those two personas hold.
TILL_REQUIRED_READS = (
	"System Settings",
	"Global Defaults",
	"Device",
	"POS Scale Settings",
	"Company",
	"Currency",
	"Branch",
	"UOM",
	"Sales Taxes and Charges Template",
	"Pricing Rule",
	"Product Bundle",
)

# Manager is catalog.ts's `allWrite()`: write everywhere, with the seven `reports.*`
# sub-keys forced down to read (nothing in the AP reads canWrite() on a report).
_ALL_WRITE = {key: "write" for key in MODULE_KEYS}
_ALL_WRITE.update({key: "read" for key in MODULE_KEYS if key.startswith("reports.")})

PERSONA_MATRIX = {
	"Manager": dict(_ALL_WRITE),
	"Branch Supervisor": {
		"dashboard": "none",
		"pos": "write",
		"products": "write",
		"inventory": "write",
		"warehouses": "read",
		"branches": "read",
		"staff": "read",
		"roles": "none",
		"attendance": "write",
		"salary": "none",
		"finance": "read",
		"reports": "read",
		"settings": "none",
		"accounting": "read",
		"customers": "write",
		"suppliers": "read",
		"reports.sales": "read",
		"reports.products": "read",
		"reports.inventory": "read",
		"reports.staff": "read",
		"reports.pos": "read",
		"reports.salary": "none",
		"reports.suppliers": "none",
	},
	"Cashier": {
		"dashboard": "none",
		"pos": "read",
		"products": "read",
		"inventory": "none",
		"warehouses": "none",
		"branches": "none",
		"staff": "none",
		"roles": "none",
		"attendance": "none",
		"salary": "none",
		"finance": "none",
		"reports": "none",
		"settings": "none",
		"accounting": "none",
		"customers": "read",
		"suppliers": "none",
		"reports.sales": "none",
		"reports.products": "none",
		"reports.inventory": "none",
		"reports.staff": "none",
		"reports.pos": "none",
		"reports.salary": "none",
		"reports.suppliers": "none",
	},
	"Accountant": {
		"dashboard": "none",
		"pos": "read",
		"products": "none",
		"inventory": "none",
		"warehouses": "none",
		"branches": "none",
		"staff": "none",
		"roles": "none",
		"attendance": "none",
		"salary": "read",
		"finance": "write",
		"reports": "read",
		"settings": "none",
		"accounting": "write",
		"customers": "read",
		"suppliers": "write",
		"reports.sales": "read",
		"reports.products": "none",
		"reports.inventory": "none",
		"reports.staff": "none",
		"reports.pos": "read",
		"reports.salary": "read",
		"reports.suppliers": "read",
	},
	"Inventory Keeper": {
		"dashboard": "none",
		"pos": "none",
		"products": "write",
		"inventory": "write",
		"warehouses": "write",
		"branches": "none",
		"staff": "none",
		"roles": "none",
		"attendance": "none",
		"salary": "none",
		"finance": "none",
		"reports": "read",
		"settings": "none",
		"accounting": "none",
		"customers": "none",
		"suppliers": "write",
		"reports.sales": "none",
		"reports.products": "read",
		"reports.inventory": "read",
		"reports.staff": "none",
		"reports.pos": "none",
		"reports.salary": "none",
		"reports.suppliers": "read",
	},
	"HR": {
		"dashboard": "none",
		"pos": "none",
		"products": "none",
		"inventory": "none",
		"warehouses": "none",
		"branches": "read",
		"staff": "read",
		"roles": "read",
		"attendance": "write",
		"salary": "write",
		"finance": "none",
		"reports": "read",
		"settings": "none",
		"accounting": "none",
		"customers": "none",
		"suppliers": "none",
		"reports.sales": "none",
		"reports.products": "none",
		"reports.inventory": "none",
		"reports.staff": "read",
		"reports.pos": "none",
		"reports.salary": "read",
		"reports.suppliers": "none",
	},
}
