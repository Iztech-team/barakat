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
	# `POS Employee Branch` is deliberately absent: it is a child table (istable=1) and
	# inherits its parent's permissions. Granting perms on one is a no-op that reads
	# like coverage.
	# `Sales Invoice` + `POS Invoice Merge Log` are here because CLOSING a shift
	# consolidates the day's POS Invoices into a Sales Invoice, and ERPNext does that
	# under the closing user's own session:
	#   erpnext/accounts/doctype/pos_invoice_merge_log/pos_invoice_merge_log.py:157
	#     sales_invoice.save(); sales_invoice.submit()   <- no ignore_permissions,
	#                                                       on a fresh frappe.new_doc
	# The work is enqueued as a background job, but frappe.enqueue defaults to running
	# as the enqueueing user. Verified against the ERPNext source on the test bench
	# 2026-07-29, after the removal diff flagged Sales Invoice as a loss and the
	# assumption "consolidation is a system job" turned out to be wrong.
	#
	# Without these, every shift close silently fails to produce books.
	"pos": (
		"POS Invoice",
		"POS Opening Entry",
		"POS Closing Entry",
		"POS Profile",
		"Device",
		"Sales Invoice",
		"POS Invoice Merge Log",
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
	# Cost Center belongs here because the setup walkthrough's `cost_center` step is
	# gated on `settings`, and its confirm route is mutate('settings'). Missing it made
	# the company-accounts page tell a Manager "1 account still needs confirmation"
	# forever — the step could not even be READ. Caught on osa 2026-07-29.
	"settings": (
		"Company",
		"Global Defaults",
		"System Settings",
		"POS Scale Settings",
		"Cost Center",
	),
	# Price List is OWNED here, not just picked: the AP's price-list routes are
	# gated on `accounting` (view/mutate), so an Accountant must be able to WRITE one.
	"accounting": (
		"Account",
		"Mode of Payment",
		"Sales Taxes and Charges Template",
		"Currency",
		"Currency Exchange",
		"Price List",
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
	# POS Invoice: the top-products report ranks by SALES, so it reads invoices.
	"reports.products": ("Item", "Bin", "POS Invoice"),
	"reports.inventory": ("Bin", "Warehouse", "Stock Ledger Entry", "Item"),
	# POS Invoice: staff performance is sales-per-cashier.
	"reports.staff": ("Employee", "Attendance", "POS Invoice"),
	"reports.pos": ("POS Invoice", "POS Closing Entry", "Branch"),
	"reports.salary": ("Salary Slip",),
	# GL Entry is deliberately NOT here. The supplier statement is a GL query, but a
	# module grant would be unscoped — handing the Inventory Keeper (finance: none)
	# every sales, payroll and journal line. It comes instead from the hand-written
	# `Barakat Supplier Ledger Reader`, whose rows are filtered to party_type=Supplier
	# by barakat.overrides.gl_entry. The Accountant reads the full ledger legitimately
	# through `finance: write`, which does carry GL Entry.
	"reports.suppliers": ("Supplier",),
}

# Doctypes that are NEVER writable, whatever the matrix says about their module.
#
# ERPNext writes these itself, from submitted vouchers, with ignore_permissions. A
# module-level write grant would hand a persona direct write on a ledger — found by the
# removal diff on 2026-07-29, where `inventory: write` was about to give Branch
# Supervisor, Inventory Keeper and Manager create+write+DELETE on Stock Ledger Entry,
# and `finance: write` the same on GL Entry.
#
# Nothing legitimate needs it: a stock adjustment goes through Stock Entry / Stock
# Reconciliation, and a payment through Journal Entry / Payment Entry. The ledger row
# and the Bin update follow automatically.
#
#   GL Entry, Stock Ledger Entry -> the two ledgers, written on submit
#   Bin                          -> cached stock levels, recomputed by the SLE
#   Loyalty Point Entry          -> written by invoice submit / redemption
#   System Settings              -> site-wide single. Already a deliberate decision:
#                                   _grant_settings_manager_perms explicitly sets write
#                                   back to 0 on it. Do not regress that here.
READ_ONLY_DOCTYPES = frozenset(
	{
		"GL Entry",
		"Stock Ledger Entry",
		"Bin",
		"Loyalty Point Entry",
		"System Settings",
	}
)

# Submittable doctypes (docstatus 0/1/2). A Writer role must carry submit+cancel on
# these or the module is write-in-name-only: the record saves as a draft and the action
# fails at the last step.
#
# Found by the removal diff on 2026-07-29, comparing against the roles a real site had:
# without this, `pos: write` could not SUBMIT a POS Invoice (every sale breaks),
# `finance: write` could not post a Journal Entry (cash movements and day-closing
# break), `suppliers: write` could not pay a Purchase Invoice, and `salary: write`
# could not submit a Salary Slip.
SUBMITTABLE_DOCTYPES = frozenset(
	{
		"POS Invoice",
		"POS Opening Entry",
		"POS Closing Entry",
		"Sales Invoice",
		"Purchase Invoice",
		"Journal Entry",
		"Payment Entry",
		"Stock Entry",
		"Stock Reconciliation",
		"Salary Slip",
		"Salary Structure",
		"Salary Structure Assignment",
		"Attendance",
		"Holiday List Assignment",
		"POS Invoice Merge Log",
	}
)

# Read-only doctypes a module's FORMS need even though the module does not own them.
#
# This mirrors the proxy's `viewAny` concept in middleware/permission.ts: the purchase
# invoice form renders an item picker and a warehouse picker, so an Accountant
# (`suppliers: write`, `products: none`) must be able to READ those lists or the form is
# unusable. The proxy already widens its route gate for exactly this; without the same
# widening in ERPNext the picker renders as a valid-looking EMPTY list with nothing on
# screen to indicate failure.
#
# READ ONLY, never write. Each entry is a picker on a form the persona can open.
SHARED_PICKER_READS = {
	"suppliers": ("Item", "Warehouse", "Account", "Mode of Payment", "Company", "Cost Center"),
	"inventory": ("Item", "Warehouse", "Company", "UOM", "Branch", "Cost Center"),
	"products": ("Company", "Warehouse"),
	# Warehouse is deliberately ABSENT. The proxy makes the same call explicitly:
	# `GET /api/warehouses` is viewAny(['warehouses','inventory','suppliers']) and its
	# comment records that including the catalogue "handed a Cashier the warehouse list
	# for nothing". The till reads warehouses under a Manager / Branch Supervisor
	# session, and both own the `warehouses` module already.
	"pos": ("Customer", "Branch", "Mode of Payment", "Price List", "Company", "Cost Center"),
	"staff": ("Branch", "Company"),
	"salary": ("Employee", "Account", "Company", "Mode of Payment"),
	"attendance": ("Employee", "Branch"),
	"finance": ("Account", "Company", "Employee", "Mode of Payment", "Cost Center"),
	"accounting": ("Company", "Price List", "Item Price", "Account", "Cost Center"),
	"customers": ("Branch", "Company", "Price List"),
	"warehouses": ("Company", "Account"),
	"branches": ("Company",),
	"settings": ("Account", "Branch"),
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


# The doctypes the admin panel's setup walkthrough reads to render its 15 steps.
#
# Declared explicitly because the guard test iterates MODULE_DOCTYPES — a doctype in NO
# module is invisible to it. `Cost Center` was exactly that: it is step 15, no module
# listed it, and the page told the Manager "1 account still needs confirmation" with no
# way to clear it. Asserted by test_setup_walkthrough_is_readable.
SETUP_WALKTHROUGH_DOCTYPES = ("Company", "Account", "Cost Center")
