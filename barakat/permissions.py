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

from barakat.persona_matrix import (
	MODULE_DOCTYPES,
	MODULE_KEYS,
	PERSONA_MATRIX,
	READ_ONLY_DOCTYPES,
	SHARED_PICKER_READS,
	SUBMITTABLE_DOCTYPES,
	TILL_REQUIRED_READS,
)

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
	#
	# It also carries the READ-ONLY device configuration a till pulls at sync time.
	# Held only by Manager and Branch Supervisor — the two personas that may log a
	# POS device in (see barakat.api.session_role.get_my_pos_role) — so this is the
	# right home for "what a till reads about itself".
	#
	# Every doctype below shipped readable by `System Manager` ONLY, which no persona
	# holds, so these pulls 403'd for EVERY POS login. The desktop app wraps each pull
	# in a try/catch, so nothing crashed — the till silently fell back to defaults,
	# which is why this went unnoticed. Measured 2026-07-28 on the fatima test site:
	#   - POS Scale Settings  -> scale barcodes never parsed, `has_balances` always 0,
	#                            so weighed items rang up as plain units on every till.
	#   - Device              -> the paired device name never resolved (always null).
	#   - System Settings /   -> Branch Supervisor only (Manager reads them through
	#     Global Defaults        `Barakat Settings Manager`). These are the FALLBACK
	#                            for rounding + the site currency; without them a
	#                            Branch Supervisor till dropped to hard-coded defaults
	#                            (legacy rounding, "ILS") while a Manager till on the
	#                            same shop used the configured values — the same sale
	#                            could round differently depending on who opened the
	#                            till. READ only: rounding is still WRITTEN through
	#                            barakat.api.settings.set_rounding_settings, which is
	#                            role-checked against ROUNDING_WRITER_ROLES, and this
	#                            role is not in it.
	"Barakat POS Operator": {
		"POS Opening Entry": ("read", "write", "create", "submit", "cancel"),
		"POS Closing Entry": ("read", "write", "create", "submit", "cancel"),
		"POS Invoice": ("read",),
		"POS Profile": ("read",),
		"POS Scale Settings": ("read",),
		"Device": ("read",),
		"System Settings": ("read",),
		"Global Defaults": ("read",),
	},
	# Attendance without `HR User`. HR User is the only native role with Attendance
	# write, but it also carries `Employee` write — Branch Supervisor is
	# `staff: read` and must not be able to edit employee records.
	#
	# submit + cancel are REQUIRED: Attendance is a submittable doctype (docstatus 1),
	# and the admin panel's "Record absence" flow creates AND submits in one step.
	# Without them a persona whose only attendance grant is this role (Branch
	# Supervisor — no native HR role) got a 403 on submit even though its matrix says
	# attendance: write. HR is unaffected (it submits via HR Manager/HR User).
	"Barakat Attendance Manager": {
		"Attendance": ("read", "write", "create", "delete", "submit", "cancel"),
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
	# Read-only POS for Accountant (`pos: read`) and Cashier (`pos: read` — their AP
	# login only ever looks things up, see the Cashier bundle note below).
	# POS Opening/Closing Entry read is native only to Sales Manager and System
	# Manager, both of which carry far more. Discovered by the persona sweep:
	# /api/pos/shifts 403'd for Accountant.
	#
	# Pricing Rule read was added here (not a new role) for the same reason: the
	# proxy's getInvoiceItems resolves a discounted line's Pricing Rule name to its
	# title under the CALLER's own session (`list('Pricing Rule', ...)`), and
	# neither Cashier nor Accountant carries any role with Pricing Rule access —
	# `Sales Manager` (which does) is deliberately excluded from both. Without this
	# the lookup 403s and the promotion title silently comes back null, which is
	# indistinguishable from "no promotion applied". Read-only: a Cashier or
	# Accountant must still not be able to edit promotions.
	"Barakat POS Viewer": {
		"POS Opening Entry": ("read",),
		"POS Closing Entry": ("read",),
		"POS Invoice": ("read",),
		"POS Profile": ("read",),
		"Pricing Rule": ("read",),
	},
	# Read-only sales/payment reference data for personas that are `finance: none`
	# but still need it underneath a report or a lookup list. Discovered by the
	# persona sweep: Inventory Keeper 403'd on /api/reports/top-products and
	# /api/suppliers/purchases/payment-modes, HR on /api/reports/staff-performance.
	# Every native role carrying these reads also carries write on the ledger.
	"Barakat Commerce Reader": {
		"Sales Invoice": ("read",),
		"POS Invoice": ("read",),
		# Payment-mode lookup on the purchase payment form.
		"Mode of Payment": ("read",),
	},
	# Purchase Invoice create/submit for the Inventory Keeper (`suppliers: write`),
	# who records incoming stock and must be able to POST the payable it creates.
	# Native Purchase Invoice create+submit is `Accounts User`/`Accounts Manager`,
	# but both carry the whole payables/GL surface (Journal Entry, Payment Entry,
	# GL write) a stock keeper must not have. This narrow role grants ONLY the
	# purchase-invoice document lifecycle; the GL/stock entries it produces are
	# system-written (ignore_permissions), and PAYING the invoice stays a finance
	# action (Payment Entry — not in this role, so the AP's "pay" control, gated on
	# `finance`, stays hidden for Inventory Keeper). Found by round-2 testing:
	# Inventory Keeper 403'd creating a Purchase Invoice while the AP showed the form.
	"Barakat Purchase Invoice Clerk": {
		"Purchase Invoice": ("read", "write", "create", "submit", "cancel"),
	},
	# Mode of Payment DELETE. No native role holds it (measured on BOM: the doctype's
	# DocPerm table has create/write but no `delete` row for anyone), so the AP's
	# accounting page could create and edit payment modes but never remove one — a
	# raw-API 403 for Accountant. Split out (not folded into a native role) so the
	# grant is exactly one capability on one doctype. Held by the `accounting: write`
	# personas (Manager, Accountant). ERPNext still blocks deleting a payment mode
	# that is referenced by any document (LinkExistsError), surfaced as a clean error.
	"Barakat Payment Mode Manager": {
		"Mode of Payment": ("read", "write", "create", "delete"),
	},
	# Customer DELETE for the Cashier (`customers: write`). Native Customer delete is
	# `Sales Master Manager`-only — Manager and Branch Supervisor hold it, but the
	# Cashier (Sales User: create/write, no delete) could create and edit customers
	# yet never remove one. This narrow role closes the gap without handing the
	# Cashier Sales Master Manager's Item Price / Price List write. ERPNext blocks
	# deleting a customer with transactions (LinkExistsError), surfaced as a clean
	# error, so this only ever removes freshly-created / empty customer records.
	"Barakat Customer Manager": {
		"Customer": ("read", "delete"),
	},
	# Customer Group CRUD. Customer Group write is natively `Sales Master Manager`
	# only. Manager and Branch Supervisor hold that role, so they can manage groups;
	# the Cashier persona (customers=write, but no Sales Master Manager — that role
	# also carries Item Price / Price List write a cashier must not have) could
	# create customers but NOT customer groups. Found by per-persona CRUD testing:
	# POST /api/customers/groups 403'd for the Cashier while the AP shows the button
	# (it gates on `customers`). This narrow role closes that without widening the
	# cashier into price-list territory.
	"Barakat Customer Group Manager": {
		"Customer Group": ("read", "write", "create", "delete"),
	},
	# Shared read-only reference data that FORMS across every module need, whatever
	# the persona's matrix row says. `Branch` is natively readable only by HR
	# Manager / HR User, but the customer, staff and attendance forms all render a
	# branch picker — so a Cashier creating a customer needs it while being
	# `branches: none`. Caught in the browser: the picker rendered with no options
	# because /api/branches 403'd from ERPNext, not from the proxy gate.
	# Held by every persona. Branch WRITE stays with HR Manager / HR User.
	"Barakat Reference Reader": {
		"Branch": ("read",),
	},
	# The supplier statement (AP report `reports.suppliers`) is a GL Entry query on
	# the supplier's party rows. GL Entry read is natively `Accounts User` /
	# `Accounts Manager` / `Auditor` only — all three carry the whole payables,
	# journal and GL-write surface an Inventory Keeper (`finance: none`,
	# `accounting: none`) must not have. This role grants GL Entry READ and nothing
	# else, and `barakat.overrides.gl_entry` narrows its holders to
	# `party_type = 'Supplier'` rows, so the grant matches the role's name: a
	# supplier ledger, not the ledger.
	#
	# Found 2026-07-27: the AP route guard and the proxy gate both let Inventory
	# Keeper onto /reports/suppliers (its matrix says `reports.suppliers: read`),
	# and the page then showed "error loading data" because the GL Entry list 403'd
	# underneath. Accountant and Manager were unaffected — both hold `Accounts User`.
	"Barakat Supplier Ledger Reader": {
		"GL Entry": ("read",),
	},
	# Read-only payroll for Accountant (`salary: read`). Every native role with
	# Salary Slip read also carries write.
	"Barakat Salary Viewer": {
		"Salary Slip": ("read",),
		"Salary Component": ("read",),
		"Salary Structure Assignment": ("read",),
		"Employee": ("read",),
	},
	# Read-only shop reference data for the Cashier persona, whose admin-panel login
	# is for LOOKING THINGS UP, not managing them (they do their till work in the
	# desktop POS under a Manager/Branch Supervisor device session, via PIN — their
	# own ERPNext session never writes). Their native write roles (Sales User,
	# Stock User) were replaced by this pure-read role so a Cashier cannot create or
	# edit customers, products, groups, etc. even via /app or the raw REST API. POS,
	# loyalty and branch reads come from Barakat POS Viewer / Loyalty Viewer /
	# Reference Reader; this covers the rest of what the read-only AP pages render.
	"Barakat Cashier Reader": {
		"Customer": ("read",),
		"Customer Group": ("read",),
		"Contact": ("read",),
		"Item": ("read",),
		"Item Group": ("read",),
		"Item Price": ("read",),
		"UOM": ("read",),
		"Product Bundle": ("read",),
	},
	# Every persona's OWN profile and OWN payslips. The DocPerm here is doctype-wide;
	# what makes it "own" is the row scoping in overrides/self_service.py. Read-only:
	# editing your own employee record or payslip is not a self-service action.
	#
	# `select` on Employee is required — the AP renders an employee picker on forms
	# a persona can open, and Frappe's list query accepts select OR read.
	"Barakat Self Service": {
		"Employee": ("read", "select"),
		"Salary Slip": ("read",),
	},
}


# ── Self service ─────────────────────────────────────────────────────────────
# Replaces the native `Employee` and `Employee Self Service` roles, which were pinned
# to EVERY persona through PRESERVED_ROLES and granted UNSCOPED read on Employee and
# Salary Slip. Measured on prod 2026-07-29: a Cashier read every salary slip
# (net_pay 2884.62) and all 138 employee records, on a site where the matrix says
# `salary: none, staff: none`.
#
# Stock ERPNext pairs those roles with a User Permission pinning the user to their own
# Employee record. barakat deletes that permission deliberately (see the note in
# overrides/staff_roles.py) because it scoped the user across EVERY doctype and hid
# other people's shifts and attendance from managers. Removing the leash without
# removing the role is what left the read unrestricted.
#
# Row scoping lives in overrides/self_service.py — a DocPerm is per-doctype and
# cannot express "only my own".
SELF_SERVICE_ROLE = "Barakat Self Service"

# Roles whose holders read these doctypes UNSCOPED.
#
# A `permission_query_conditions` hook applies to EVERY user of that doctype, not just
# the persona it was written for. Without this stand-down list the hook would narrow HR
# and Manager to their own record and break payroll outright — the exact "forgot a
# permission someone needs" failure this redesign exists to avoid.
UNSCOPED_BY_DOCTYPE = {
	"Employee": frozenset(
		{
			"Barakat Staff Reader",
			"Barakat Staff Writer",
			"Barakat Reports Staff Reader",
			"Barakat Attendance Manager",
			"Barakat Salary Viewer",
			# NOT `Barakat Salary Reader`. The asymmetry with the Salary Slip list
			# below is deliberate and is pinned by
			# test_accountant_reads_salary_unscoped_but_staff_scoped: a payroll reader
			# sees the PAY, not the personnel file. The slip carries `employee_name`,
			# so the salary report renders without Employee read.
			#
			# Measured on osa 2026-07-30 and reported as a leak: the Accountant sees
			# 8 salary slips but only 1 employee (their own). That reads as incoherent
			# from the outside and is worth restating — it is the intended split, and
			# the QA brief line "no role except Manager and HR sees other people's
			# payslips" is what disagrees with the matrix, which grants Accountant
			# both `salary: read` and `reports.salary: read`.
			"System Manager",
			"Administrator",
		}
	),
	"Salary Slip": frozenset(
		{
			"Barakat Salary Reader",
			"Barakat Salary Writer",
			"Barakat Reports Salary Reader",
			"Barakat Salary Viewer",
			"System Manager",
			"Administrator",
		}
	),
}


def self_scope_applies(doctype, roles):
	"""True when this caller must be narrowed to their own rows on `doctype`.

	Pure decision — no Frappe — so it is unit-testable off a bench.
	"""
	unscoped = UNSCOPED_BY_DOCTYPE.get(doctype)
	if unscoped is None:
		return False
	role_set = set(roles)
	if role_set & unscoped:
		return False
	return SELF_SERVICE_ROLE in role_set


# --------------------------------------------------------------------------
# Generated module-capability roles
# --------------------------------------------------------------------------

# `select` is granted alongside `read` on purpose. Frappe's list query permits
# `select` OR `read` (DatabaseQuery.check_read_permission), and link-field pickers
# rely on `select` alone. Measured on niveen1 2026-07-29: a cashier lists 98 account
# NAMES with read denied and the document open refused — that behaviour is correct and
# must survive. Dropping `select` empties dropdowns with no error in the AP and no
# error in the till.
#
# `report` is granted alongside `read` for the same class of reason. It is a VIEW
# capability, not a data grant: Frappe checks it in two places only — the desk's
# report view, and `frappe.desk.query_report.run`, which requires
# `has_permission(<report>.ref_doctype, "report")` on top of the report's own role
# list. Rows stay bounded by `read`, the query-condition hooks and User Permissions,
# so this opens no record a persona could not already list. Without it the AP's
# trial balance 403'd for every persona including Manager, whose matrix row says
# `finance: write` (measured on the osa test site, 2026-07-30) — and because gate 1
# below is independent, granting `report` does NOT make ERPNext's report catalogue
# runnable.
READER_PERMS = ("read", "select", "report")
WRITER_PERMS = ("read", "select", "report", "write", "create", "delete")

# Module keys that are acronyms. Role names show in the ERPNext desk's role list, and
# a plain .capitalize() renders "pos" as "Pos" — which reads as a typo next to the
# hand-written `Barakat POS Operator` / `Barakat POS Viewer`.
_ACRONYMS = {"pos": "POS"}


def role_name_for(module, level):
	"""The generated role for a (module, level) cell, or None when nothing is granted.

	Returns None for `none`, and for modules that map to no ERPNext doctype
	(`dashboard`, `reports`, `roles` — AP-only gates).
	"""
	if level not in ("read", "write"):
		return None
	if not MODULE_DOCTYPES.get(module):
		return None
	words = " ".join(_ACRONYMS.get(part, part.capitalize()) for part in module.split("."))
	return f"Barakat {words} {'Writer' if level == 'write' else 'Reader'}"


def perms_for(doctype, level):
	"""The perms a generated role grants on one doctype.

	READ_ONLY_DOCTYPES are clamped to read even inside a Writer role — ERPNext writes
	those itself from submitted vouchers, and a module-level write grant would hand a
	persona direct write on a ledger.

	SUBMITTABLE_DOCTYPES additionally get submit+cancel, without which `write` means
	"can save a draft and nothing more".
	"""
	if level != "write" or doctype in READ_ONLY_DOCTYPES:
		return READER_PERMS
	if doctype in SUBMITTABLE_DOCTYPES:
		return WRITER_PERMS + ("submit", "cancel")
	return WRITER_PERMS


def _build_module_role_perms():
	"""One Reader and one Writer role per module that owns doctypes.

	Both levels also carry the module's SHARED_PICKER_READS as read-only, so a form's
	pickers populate for a persona that does not own the picked doctype.
	"""
	out = {}
	for module, doctypes in MODULE_DOCTYPES.items():
		if not doctypes:
			continue
		shared = tuple(d for d in SHARED_PICKER_READS.get(module, ()) if d not in doctypes)
		for level in ("read", "write"):
			perms = {dt: perms_for(dt, level) for dt in doctypes}
			for dt in shared:
				# Never upgrade a doctype the module already owns, and never write.
				perms.setdefault(dt, READER_PERMS)
			out[role_name_for(module, level)] = perms
	return out


MODULE_ROLE_PERMS = _build_module_role_perms()

# A generated name must never shadow a hand-written role — the hand-written one carries
# capabilities the generator cannot express (submit/cancel, row scoping).
_OVERLAP = set(BARAKAT_ROLE_PERMS).intersection(MODULE_ROLE_PERMS)
assert not _OVERLAP, f"generated role name collides with a hand-written one: {sorted(_OVERLAP)}"

# Hand-written first, generated second. This is what setup/install.py provisions.
ALL_ROLE_PERMS = {**BARAKAT_ROLE_PERMS, **MODULE_ROLE_PERMS}

BARAKAT_CUSTOM_ROLES = tuple(ALL_ROLE_PERMS)

# Barakat roles whose DocPerms are granted by their OWN dedicated installer function
# rather than by the ALL_ROLE_PERMS loop, because what they grant is conditional or
# permlevel-sensitive:
#   - Barakat Settings Manager -> _grant_settings_manager_perms (read+write on two
#     singles, and it must NOT get write on System Settings)
#   - Barakat Staff Manager    -> _grant_staff_manager_perms (User write at permlevel 0
#     only; see the permlevel-1 note in overrides/staff_roles.py)
#   - Barakat POS PIN Reader   -> _grant_pos_pin_permlevel (Employee read+write at
#     permlevel 1, which is where custom_pos_pin lives; the ALL_ROLE_PERMS loop only
#     ever grants permlevel 0)
# They are real roles and must still be exported in the hooks.py fixture.
EXTERNALLY_PERMED_ROLES = frozenset(
	{"Barakat Settings Manager", "Barakat Staff Manager", "Barakat POS PIN Reader"}
)

# Persona preset names. `Employee.custom_role_preset` is a Link to `Role`, so a Role
# record of the same name must exist or the Employee cannot be saved at all.
#
# DERIVED from the matrix, never hand-listed. The hand-written list in
# setup/install.py omitted "Manager" — so on a freshly created site the Manager persona
# could not be assigned at all:
#     LinkValidationError: Could not find Role: Manager
# Older sites only work because someone created that Role by hand at some point.
# Caught on the fresh `osa` test site, 2026-07-29.
PERSONA_PRESET_ROLES = tuple(sorted(PERSONA_MATRIX))

# Every Role this app must ship in its hooks.py fixture. Computed, never hand-listed:
# a role that is not exported is silently dropped by persona_role_bundle(), leaving the
# user with fewer roles and no error anywhere.
EXPORTED_ROLE_NAMES = sorted(
	{*PERSONA_PRESET_ROLES, *ALL_ROLE_PERMS, *EXTERNALLY_PERMED_ROLES}
)


# ── Native ERPNext reports the AP runs ───────────────────────────────────────
# A DocPerm is not enough to run one of ERPNext's own query reports. Frappe gates
# `frappe.desk.query_report.run` twice, independently (frappe/desk/query_report.py,
# `get_report_doc`):
#
#   gate 1  `Report.is_permitted()` — the caller must hold a role from the report's
#           OWN allow-list. ERPNext ships "Trial Balance" allowing Accounts Manager,
#           Accounts User and Auditor, none of which any persona holds any more.
#   gate 2  `has_permission(report.ref_doctype, "report")` — covered by the `report`
#           ptype now in READER_PERMS.
#
# Both failed for every persona on osa (2026-07-30), so /finance/general-ledger's
# Trial Balance tab 403'd for a Manager whose matrix row is `finance: write`.
#
# Gate 1 is closed with a `Custom Role`, not by editing the Report: standard reports
# are re-synced from their app's JSON on every migrate, so roles appended to the
# Report's own child table are wiped. `Custom Role` is Frappe's designed override and
# survives.
#
# CAREFUL — `is_permitted` REPLACES rather than extends when a Custom Role exists:
#
#     if custom_roles:
#         allowed = custom_roles
#
# so the report's native roles must be carried across or the accounts staff and any
# owner relying on them lose the report. `report_allowed_roles` is what guarantees
# that; it is pure so it can be tested off a bench.
NATIVE_REPORT_MODULES = {
	# AP: /finance/general-ledger, Trial Balance tab.
	# Proxy: GET /api/finance/trial-balance -> view('finance').
	"Trial Balance": ("finance",),
}

# Always allowed on top of the native + generated roles. An owner account holds no
# persona and acts under its own native roles, so without this a site whose owner is
# not also an accounts user loses the report — the same owner blind spot
# `_grant_owner_payment_mode_perms` was written for.
REPORT_FALLBACK_ROLES = ("System Manager",)


def report_allowed_roles(report, native_roles=()):
	"""Every role that must appear in `report`'s Custom Role allow-list.

	`native_roles` is the report's existing `Has Role` list, carried across because a
	Custom Role replaces it. Returns () for a report this app does not claim, which is
	the signal to leave it alone entirely.
	"""
	modules = NATIVE_REPORT_MODULES.get(report)
	if not modules:
		return ()
	out = []
	for name in (*native_roles, *REPORT_FALLBACK_ROLES):
		if name and name not in out:
			out.append(name)
	for module in modules:
		for level in ("read", "write"):
			name = role_name_for(module, level)
			if name and name not in out:
				out.append(name)
	return tuple(out)


# ── User visibility: yourself, or your own shop's staff ───────────────────────
# `read` on `User` comes from Frappe's own baseline for a desk session, not from any
# Barakat role, so nothing in the persona matrix ever covered it — the same blind spot
# that hid `Cost Center`: a doctype in NO module is invisible to the matrix guards.
#
# Measured on bom.iztech.net (prod, 2026-07-30) — and this is a cross-SHOP leak, not
# just a directory listing:
#   * a Cashier saw 26 of the site's 28 users, every colleague's name and email;
#   * the site hosts EIGHT companies, and `bom3manager@gmail.com` — Manager of BOM3,
#     which has one employee — saw 21 users belonging to BOM, a different shop.
#
# Three tiers, because "who may see a person" is not one question:
#   self         — no staff access at all (Cashier, Accountant, Inventory Keeper).
#                  They never need a colleague list; the documents they read carry the
#                  names they display (a payslip has employee_name).
#   company      — holds staff/attendance/payroll access (Manager, HR, Branch
#                  Supervisor). Scoped to the companies their OWN Employee records sit
#                  in, so one shop's manager cannot enumerate another shop's staff.
#   unrestricted — owner / System Manager / Administrator. Standing down for them is
#                  not an optimisation: a query-condition hook applies to EVERY caller,
#                  and narrowing the owner would lock them out of their own site.
USER_SCOPE_UNRESTRICTED_ROLES = frozenset({"System Manager", "Administrator"})

USER_SCOPE_COMPANY_ROLES = frozenset(
	{
		"Barakat Staff Reader",
		"Barakat Staff Writer",
		"Barakat Staff Manager",
		"Barakat Reports Staff Reader",
		"Barakat Attendance Manager",
		"Barakat Salary Viewer",
	}
)


def user_scope_for(caller_roles):
	"""'unrestricted' | 'company' | 'self' — which slice of `User` this caller may see.

	Pure decision, no Frappe, so it is unit-testable off a bench.
	"""
	roles = set(caller_roles)
	if roles & USER_SCOPE_UNRESTRICTED_ROLES:
		return "unrestricted"
	if roles & USER_SCOPE_COMPANY_ROLES:
		return "company"
	return "self"


# ── GL Entry row scoping for the supplier-ledger role ────────────────────────
# `Barakat Supplier Ledger Reader` is a DocPerm grant, and a DocPerm is all-or-
# nothing per doctype: it would hand a stock keeper every sales, payroll and
# journal line in the ledger. The row filter below (wired up in hooks.py through
# `barakat.overrides.gl_entry`) is what keeps the grant to supplier rows only.
SUPPLIER_LEDGER_ROLE = "Barakat Supplier Ledger Reader"

# Callers whose GL access does NOT come from the narrow role are never narrowed —
# these already read the whole ledger natively, so filtering them would silently
# break the accounting pages and the desk's own reports.
GL_UNRESTRICTED_ROLES = frozenset(
	{"Accounts User", "Accounts Manager", "Auditor", "System Manager", "Administrator"}
)


def gl_entry_scope_for(caller_roles):
	"""'supplier' when this caller's only route to GL Entry is the supplier-ledger
	role, else None (read the ledger unfiltered / not at all, as DocPerms decide).

	Pure decision — no Frappe — so it is unit-testable off a bench.
	"""
	roles = set(caller_roles)
	if SUPPLIER_LEDGER_ROLE not in roles:
		return None
	if roles & GL_UNRESTRICTED_ROLES:
		return None
	return "supplier"


# --------------------------------------------------------------------------
# Persona bundles
# --------------------------------------------------------------------------

# Persona (Employee.custom_role_preset) -> the ERPNext roles it receives.
#
# Derived from the admin panel module matrix (proxy-barakat
# src/modules/roles/catalog.ts) crossed with the site's actual DocPerm table.
# Roles are NEVER granted here that carry full-admin or code-execution reach:
# `System Manager`, `Script Manager` and `Report Manager` appear in no bundle.
# Hand-written roles a persona keeps ON TOP of its generated matrix roles. These are
# capabilities the matrix cannot express:
#   - Barakat POS Operator: the shift lifecycle (submit/cancel) plus the till's own
#     device reads (TILL_REQUIRED_READS). Only Manager and Branch Supervisor may log a
#     POS device in, so only they hold it.
#   - Barakat Self Service: the caller's OWN profile and payslips, row-scoped by
#     barakat.overrides.self_service. Replaces the native Employee / Employee Self
#     Service roles, which granted the same reads UNSCOPED to every persona.
#   - Barakat Staff Manager: creating logins / assigning role presets. Manager only.
#   - Barakat Purchase Invoice Clerk: Purchase Invoice submit+cancel, which the
#     generated suppliers Writer cannot grant (the generator has no submit).
#   - Barakat Supplier Ledger Reader: GL Entry filtered to supplier rows, for the
#     supplier statement without handing over the whole ledger.
#   - Barakat Attendance Manager: Attendance submit+cancel (a submittable doctype -
#     the AP's "record absence" flow creates AND submits in one step).
#   - Barakat Loyalty Manager / Viewer, Currency Manager, Payment Mode Manager,
#     Settings Manager: capabilities whose native equivalents carry far more.
#   - Barakat POS PIN Reader: the ONLY role that can read or write
#     `Employee.custom_pos_pin`, which sits at permlevel 1. It exists because the
#     generated `staff: read` role cannot tell a Branch Supervisor from HR — both hold
#     it — and a till genuinely needs its branch's PINs to authenticate cashiers
#     offline, while HR has no business reading anyone's credential. Manager and Branch
#     Supervisor only.
EXTRA_ROLES = {
	"Manager": (
		"Barakat POS Operator",
		"Barakat POS PIN Reader",
		"Barakat Staff Manager",
		"Barakat Self Service",
		"Barakat Purchase Invoice Clerk",
		"Barakat Attendance Manager",
		"Barakat Loyalty Manager",
		"Barakat Currency Manager",
		"Barakat Payment Mode Manager",
		"Barakat Settings Manager",
	),
	"Branch Supervisor": (
		"Barakat POS Operator",
		"Barakat POS PIN Reader",
		"Barakat Self Service",
		"Barakat Attendance Manager",
		"Barakat Loyalty Viewer",
	),
	"Cashier": (
		"Barakat Self Service",
		"Barakat Loyalty Viewer",
	),
	"Accountant": (
		"Barakat Self Service",
		"Barakat Purchase Invoice Clerk",
		"Barakat Currency Manager",
		"Barakat Payment Mode Manager",
		"Barakat Loyalty Viewer",
	),
	"Inventory Keeper": (
		"Barakat Self Service",
		"Barakat Purchase Invoice Clerk",
		"Barakat Supplier Ledger Reader",
	),
	"HR": (
		"Barakat Self Service",
		"Barakat Attendance Manager",
	),
}


def _build_persona_bundles():
	"""Transcribe each matrix row into generated roles, then add the extras.

	This is the whole point of the redesign: the bundle is no longer hand-picked from
	native ERPNext roles (which bundle far more than the matrix intends) but derived
	mechanically from PERSONA_MATRIX. `test_bundle_is_the_matrix_row` guards it.
	"""
	out = {}
	for persona, row in PERSONA_MATRIX.items():
		roles = []
		for module in MODULE_KEYS:
			name = role_name_for(module, row[module])
			if name and name not in roles:
				roles.append(name)
		for name in EXTRA_ROLES.get(persona, ()):
			if name not in roles:
				roles.append(name)
		out[persona] = tuple(roles)
	return out


PERSONA_ROLE_BUNDLES = _build_persona_bundles()

PERSONAS = frozenset(PERSONA_ROLE_BUNDLES)

# The personas that operate a till, and therefore the only ones that may hold a POS
# PIN.
#
# A PIN is a credential for the desktop POS and nothing else. An Accountant, an HR
# clerk or an Inventory Keeper has no use for one — and until 2026-08-17 kept a
# WORKING one after being moved off the till, because the admin panel hid the field
# and nothing else ever looked at the role again.
#
# An ALLOW-LIST, deliberately: a blank or unrecognised preset is not a POS role. A
# deny-list fails open — a persona added to the catalog next year would be a till
# operator until somebody remembered to list it. Measured on the production bench
# before choosing: no employee with a blank preset holds a PIN on any site, so the
# strict reading locks nobody out.
#
# Three other repos keep their own copy, because none of them can import this one.
# Change one, change all four:
#   proxy-barakat/src/modules/roles/catalog.ts            POS_PERSONAS
#   admin_panel_barakat/src/schemas/pages/staff/staff.ts  PIN_ROLES
#   electrobun-pos/src/bun/auth/pos-roles.ts              POS_ROLES
POS_PERSONAS = frozenset({"Manager", "Branch Supervisor", "Cashier"})

assert POS_PERSONAS <= PERSONAS, (
	f"POS_PERSONAS names personas that do not exist: {sorted(POS_PERSONAS - PERSONAS)}"
)


def is_pos_persona(preset):
	"""True when this preset may hold a POS PIN. Blank and unknown are False."""
	return (preset or "").strip() in POS_PERSONAS


def pin_role_decision(preset, new_pin, stored_pin, system_context=False):
	"""What a save should do with the PIN it is carrying: keep, clear or refuse.

	Pure, so the rule can be read and tested without a bench —
	`validations.enforce_pin_role` is the only thing that knows about documents.

	The two non-POS outcomes are deliberately different, and the difference is
	whether the PIN is being SET or merely carried along:

	- **clear** — the PIN equals what is already on record, so nobody typed it. The
	  role is what changed. Clearing it silently is right: interrupting a manager
	  with an error about a field the admin panel does not even show them would be a
	  worse bug than the one this closes.
	- **refuse** — the PIN differs from the stored one, so this save is trying to
	  give a till credential to somebody who cannot use a till. Reachable from the
	  ERPNext desk and from a hand-made API call, never from the admin panel.
	  Silently discarding what a person typed is its own bug.

	`system_context` (install / migrate / patch) never refuses. A migration must not
	die on this rule — it clears instead, the same stand-down `guard_role_preset`
	makes for the same reason.
	"""
	if is_pos_persona(preset):
		return "keep"

	new_pin = (new_pin or "").strip()
	if not new_pin:
		return "keep"

	if new_pin == (stored_pin or "").strip():
		return "clear"

	return "clear" if system_context else "refuse"

# Roles never stripped from a persona user even though no bundle names them.
#
# EMPTY as of 2026-07-29. This used to hold {"Employee", "Employee Self Service"} on
# the reasoning that ERPNext re-attaches `Employee` when an Employee record links to a
# User, and that `Employee Self Service` backed the my-profile / my-payslip views.
# Both roles grant UNSCOPED read on Employee and Salary Slip, so preserving them handed
# every persona — including the Cashier — the whole staff directory and every salary.
# That was measured on production, not theorised.
#
# The my-profile / my-payslip views are now served by SELF_SERVICE_ROLE, which grants
# the same reads scoped to the caller's own rows. ERPNext may still re-attach
# `Employee` on an Employee save; `reassert_persona_roles` rewrites the roles child
# table to exactly the bundle, so it is stripped again on the next save.
PRESERVED_ROLES = frozenset()

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


# ── Who may assign an Employee's persona preset ──────────────────────────────
# `Employee.custom_role_preset` drives the whole role bundle a user receives, so
# setting or changing it is a privileged act. Only a staff-admin (the Manager
# persona, via STAFF_MANAGER_ROLE) or the owner/System Manager may do it — a
# limited persona such as HR must not be able to stamp someone "Accountant" and
# hand out GL write it does not otherwise have.
STAFF_MANAGER_ROLE = "Barakat Staff Manager"

# Roles whose holder may set/change a persona preset. Administrator bypasses
# separately (see `may_assign_preset`).
PRESET_ASSIGN_ROLES = frozenset({STAFF_MANAGER_ROLE, "System Manager"})


def may_assign_preset(caller_roles, *, is_administrator=False, is_system_context=False):
	"""Whether a caller may set or change an Employee's `custom_role_preset`.

	Pure decision — no Frappe. `caller_roles` is any iterable of role names.
	`is_administrator` covers the Administrator account; `is_system_context`
	covers install / migrate / patch flows that run elevated.
	"""
	if is_administrator or is_system_context:
		return True
	return not PRESET_ASSIGN_ROLES.isdisjoint(set(caller_roles))


# ── Who may untick `Employee.create_user_permission` ─────────────────────────
# That checkbox is what makes ERPNext create the `Company` User Permission which IS
# the tenant boundary — unticking it deletes the row and unscopes the user from
# every shop on the site, while the field's own description only mentions employee
# records. `overrides.staff_roles.reassert_company_user_permission` already re-adds
# the row on the same save, so an untick is a no-op today. This is the second lock:
# it refuses the change outright, so the hole cannot reopen if that hook is ever
# altered, and the person doing it gets told rather than silently ignored.
#
# Narrower than PRESET_ASSIGN_ROLES on purpose: STAFF_MANAGER_ROLE (the Manager
# persona) may create and edit staff but must NOT be able to unscope one, because a
# Manager can only see their own shop's staff and this is the one field that would
# let them hand someone the whole site.
UNTICK_USER_PERMISSION_ROLES = frozenset({"System Manager"})


def user_permission_untick_allowed(
	*,
	preset,
	user_id,
	create_user_permission,
	caller_roles,
	is_administrator=False,
	is_system_context=False,
):
	"""Whether this save may leave `create_user_permission` unticked.

	Pure decision — no Frappe. True means "allowed, do nothing".

	Phrased as "may this document be SAVED unticked" rather than "did the field
	change", because `Document.has_value_changed` returns True for every field on an
	insert. A changed-based rule would therefore refuse the Manager's ordinary staff
	CREATE, where the field is simply taking its default.

	Stands down whenever the flag cannot matter, so no unrelated save (a salary edit,
	an HR-owned employee, a staff member with no login) starts failing:
	  * already ticked          — the boundary is intact
	  * no persona preset       — not admin-panel staff; the re-assert hook skips these too
	  * no linked login         — there is no User Permission for it to delete
	"""
	if create_user_permission:
		return True
	if (preset or "").strip() not in PERSONAS:
		return True
	if not (user_id or "").strip():
		return True
	if is_administrator or is_system_context:
		return True
	return not UNTICK_USER_PERMISSION_ROLES.isdisjoint(set(caller_roles))
