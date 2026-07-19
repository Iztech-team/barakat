"""Single source of truth for what each AP persona may do in ERPNext.

Two things live here, deliberately in one file so they cannot drift apart:

1. `BARAKAT_ROLE_PERMS` — the narrow custom roles this app mints, and the exact
   DocPerms each one carries. Minted because no native ERPNext role covers the
   capability without also handing out far more (see the notes on each role).
2. `PERSONA_ROLE_BUNDLES` — the explicit allow-list of ERPNext roles each admin
   panel persona receives.

## Why an allow-list

Until 2026-07-19 the bundle was "every enabled role on the site except a five-name
deny-list". Measured on the BOM site that gave an HR clerk 57 roles including
`System Manager`, `Script Manager` (arbitrary Python) and `Report Manager` — six
more roles than the tenant owner held. The admin panel's client-side matrix was
the only thing standing in the way, and the ERPNext desk at /app bypasses it
entirely.

See `proxy-barakat/docs/superpowers/specs/2026-07-19-real-roles-and-permissions-design.md`.

## Multi-site safety

A hard-coded bundle previously broke staff creation on every site that lacked the
roles it named (`LinkValidationError`, see the history note in
`overrides/staff_roles.py`). So the bundle is always **intersected with the roles
that actually exist on the site** before it is applied. A role this file names but
the site does not have is skipped, never fatal.

## Known, accepted over-grants

Native ERPNext roles bundle capabilities that the AP matrix separates. Where
splitting them would mean minting a custom role for a small gain, the native role
is used and the over-grant is recorded here:

- `Item Manager` carries `Warehouse` write. Branch Supervisor and Cashier get it
  for `Item` write but are `warehouses: read` in the matrix. The proxy gate denies
  the warehouse endpoints; the residue is only reachable via the ERPNext REST API.
- `HR Manager` / `HR User` carry `Branch` write. HR persona is `branches: read`.
  Same containment.

These are documented rather than fixed because the risk they carry is bounded and
naming a custom role per split multiplies the surface that has to stay correct.
`System Manager` and `Script Manager` — the grants that actually mattered — are
gone from every persona.
"""

# --------------------------------------------------------------------------
# Custom roles
# --------------------------------------------------------------------------

# role -> {doctype: (perm, ...)}. Every perm named here is set to 1; perms not
# named are left alone. Applied through frappe.permissions.add_permission, which
# copies the doctype's existing standard DocPerms into Custom DocPerm first —
# critical, because adding a Custom DocPerm otherwise REPLACES the standard perms
# and would silently strip System Manager's own access.
BARAKAT_ROLE_PERMS = {
	# POS shift lifecycle without `Sales Manager`. Sales Manager is the only native
	# role holding POS Opening/Closing Entry, but it also carries `Pricing Rule`
	# write — a Cashier must not be able to edit promotions.
	"Barakat POS Operator": {
		"POS Opening Entry": ("read", "write", "create", "submit", "cancel"),
		"POS Closing Entry": ("read", "write", "create", "submit", "cancel"),
		"POS Invoice": ("read",),
		"POS Profile": ("read",),
	},
	# Attendance without `HR User`. HR User is the only native role with Attendance
	# write, but it also carries `Employee` write — Branch Supervisor is
	# `staff: read` and must not be able to edit employee records.
	"Barakat Attendance Manager": {
		"Attendance": ("read", "write", "create", "delete"),
		"Employee": ("read",),
		"Branch": ("read",),
	},
	# Loyalty Program ships with a single DocPerm row: System Manager. Split into
	# manager/viewer so the four personas that are `customers: read`-or-better can
	# load the loyalty pages without write access. This is the follow-up the note
	# at setup/install.py:230-235 already recorded as owed.
	"Barakat Loyalty Manager": {
		"Loyalty Program": ("read", "write", "create", "delete"),
		"Loyalty Point Entry": ("read",),
	},
	"Barakat Loyalty Viewer": {
		"Loyalty Program": ("read",),
		"Loyalty Point Entry": ("read",),
	},
	# Currency write is System Manager-only natively, but the AP accounting page
	# edits exchange rates. Separated from `Barakat Settings Manager` so Accountant
	# can hold it without also getting System Settings / Global Defaults.
	"Barakat Currency Manager": {
		"Currency": ("read", "write", "create"),
		"Currency Exchange": ("read", "write", "create", "delete"),
	},
	# Read-only payroll for Accountant (`salary: read`). Every native role with
	# Salary Slip read also carries write.
	"Barakat Salary Viewer": {
		"Salary Slip": ("read",),
		"Salary Component": ("read",),
		"Salary Structure Assignment": ("read",),
		"Employee": ("read",),
	},
}

BARAKAT_CUSTOM_ROLES = tuple(BARAKAT_ROLE_PERMS)


# --------------------------------------------------------------------------
# Persona bundles
# --------------------------------------------------------------------------

# Persona (Employee.custom_role_preset) -> the ERPNext roles it receives.
#
# Derived from the admin panel module matrix (proxy-barakat
# src/modules/roles/catalog.ts) crossed with the site's actual DocPerm table.
# Roles are NEVER granted here that carry full-admin or code-execution reach:
# `System Manager`, `Script Manager` and `Report Manager` appear in no bundle.
PERSONA_ROLE_BUNDLES = {
	# Everything write, reports read. The tenant's day-to-day administrator.
	"Manager": (
		"Accounts Manager",
		"Accounts User",
		"Sales Manager",
		"Sales Master Manager",
		"Sales User",
		"Stock Manager",
		"Stock User",
		"Item Manager",
		"Purchase Manager",
		"Purchase Master Manager",
		"Purchase User",
		"HR Manager",
		"HR User",
		"Barakat Settings Manager",
		"Barakat Staff Manager",
		"Barakat POS Operator",
		"Barakat Attendance Manager",
		"Barakat Loyalty Manager",
		"Barakat Currency Manager",
	),
	# pos/products/inventory/attendance/customers write; warehouses, branches,
	# staff, finance, accounting, suppliers, reports read.
	"Branch Supervisor": (
		"Sales Manager",
		"Sales Master Manager",
		"Sales User",
		"Stock Manager",
		"Stock User",
		"Item Manager",
		"Accounts User",
		"Purchase User",
		"Barakat POS Operator",
		"Barakat Attendance Manager",
		"Barakat Loyalty Viewer",
	),
	# pos + customers write, products read. Nothing else.
	"Cashier": (
		"Sales User",
		"Stock User",
		"Barakat POS Operator",
		"Barakat Loyalty Viewer",
	),
	# finance/accounting/suppliers write; pos, salary, customers, reports read.
	"Accountant": (
		"Accounts Manager",
		"Accounts User",
		"Purchase Manager",
		"Purchase Master Manager",
		"Purchase User",
		"Barakat Currency Manager",
		"Barakat Salary Viewer",
		"Barakat Loyalty Viewer",
	),
	# products/inventory/warehouses/suppliers write; reports read.
	"Inventory Keeper": (
		"Item Manager",
		"Stock Manager",
		"Stock User",
		"Purchase Manager",
		"Purchase Master Manager",
		"Purchase User",
	),
	# staff/attendance/salary write; branches, roles, reports read.
	"HR": (
		"HR Manager",
		"HR User",
		"Barakat Staff Manager",
		"Barakat Attendance Manager",
	),
}

PERSONAS = frozenset(PERSONA_ROLE_BUNDLES)

# Roles that must never reach a staff persona, whatever else changes. Asserted
# below so a careless edit to a bundle fails at import rather than in production.
FORBIDDEN_ROLES = frozenset(
	{
		"Administrator",
		"System Manager",
		"Script Manager",
		"Report Manager",
		# Tenant-owner roles that exist only on some sites (e.g. pos2). Granting
		# these would make a cashier a tenant owner on the sites that define them.
		"Baraka Owner",
		"Baraka Branch",
	}
)

for _persona, _roles in PERSONA_ROLE_BUNDLES.items():
	_leaked = FORBIDDEN_ROLES.intersection(_roles)
	assert not _leaked, f"persona {_persona!r} must not be granted {sorted(_leaked)}"
del _persona, _roles


def bundle_for(persona):
	"""The roles a persona should hold. Empty tuple for an unknown persona."""
	return PERSONA_ROLE_BUNDLES.get(persona, ())
