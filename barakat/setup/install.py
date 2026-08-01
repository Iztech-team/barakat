import frappe


def after_install():
	for fn in [
		_enable_negative_stock,
		_set_session_expiry,
		_set_pos_invoice_type,
		_set_commercial_rounding,
		_create_default_customer,
		_provision_barakat_roles,
		_ensure_persona_desk_access,
		_grant_settings_manager_perms,
		_grant_loyalty_manager_perms,
		_grant_staff_manager_perms,
		_grant_barakat_role_perms,
		_ensure_native_report_roles,
		_grant_owner_payment_mode_perms,
		_relax_demo_company_user_perm,
		_lock_create_user_permission_field,
	]:
		try:
			fn()
		except Exception as e:
			frappe.log_error(f"barakat after_install: {fn.__name__} failed: {e}", "Install")
	frappe.db.commit()


def after_setup_wizard(args=None):
	for fn in [
		_create_misc_item,
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
	#
	# _provision_barakat_roles + _grant_settings_manager_perms run here too so
	# EXISTING tenants (installed before this change) pick up the new
	# `Barakat Settings Manager` role and its System Settings / Global Defaults
	# permissions on the next `bench migrate` — no reinstall needed, and the
	# grant survives future migrations (it's re-asserted, idempotently, each run).
	for fn in [
		_create_misc_item,
		_provision_barakat_roles,
		_ensure_persona_desk_access,
		_grant_settings_manager_perms,
		_grant_loyalty_manager_perms,
		_grant_staff_manager_perms,
		_grant_barakat_role_perms,
		_revoke_stale_barakat_perms,
		_ensure_native_report_roles,
		_grant_owner_payment_mode_perms,
		_relax_demo_company_user_perm,
		_lock_create_user_permission_field,
		# Last: applies the bundles the passes above have just provisioned.
		_backfill_persona_roles,
	]:
		try:
			fn()
		except Exception as e:
			frappe.log_error(f"barakat after_migrate: {fn.__name__} failed: {e}", "Install")
	frappe.db.commit()


# WIRED INTO after_migrate as of 2026-07-29. It was deliberately manual before, on the
# reasoning that a subtractive pass must not silently change what an unverified tenant's
# staff can do. What changed: leaving it manual is how the 2026-07-29 finding survived —
# sites that were never backfilled kept the wide bundles, and a cashier on one of them
# read every payslip. A hole left half-closed is not closed.
#
# What makes running it unattended acceptable now:
#   - the bundles are derived mechanically from PERSONA_MATRIX, not hand-picked, and
#     guarded both ways by barakat.test_persona_matches_matrix;
#   - every change is written to the Error Log, so what a migrate did to a site is
#     recoverable after the fact rather than invisible.
#
# The public entry point below remains, for re-running it against one site by hand:
#
#   bench --site <site> execute barakat.setup.install.backfill_persona_roles
def backfill_persona_roles():
	"""Public entry point for the subtractive backfill. See the note above."""
	_backfill_persona_roles()
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


# _fix_stock_adjustment_accounts lived here. It forced every Stock Adjustment
# account on the site to root_type=Asset / report_type=Balance Sheet via
# frappe.db.set_value (bypassing Account.validate), on install, on setup-wizard
# completion, and after EVERY migrate.
#
# It was written to stop `OpeningEntryAccountError` when adding a product to a
# fresh site. That diagnosis was wrong. ERPNext raises it only when the
# reconciliation's purpose is "Opening Stock" OR the SITE has zero Stock Ledger
# Entries (stock_reconciliation.py, validate_expense_account) — a once-per-site
# condition, not per company and not per fiscal year. The real trigger was the
# product form submitting a fake qty=1 stock count to force a Bin into existence,
# which made product creation the site's first stock transaction.
#
# The cost was permanent: stock adjustments are an expense, so with the account
# forced onto the Balance Sheet, damage and count losses never reduced profit on
# any shop. See docs/superpowers/specs/2026-07-27-stock-story-design.md (proxy
# repo) and the repair patch in barakat/patches/repair_stock_adjustment_accounts.py.


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


# The persona preset Roles. DERIVED from the matrix, never hand-listed: this list
# used to omit "Manager", so a freshly created site could not assign the Manager
# persona at all (Employee.custom_role_preset is a Link to Role, and the record simply
# did not exist). Older sites only work because someone made that Role by hand.
from barakat.permissions import PERSONA_PRESET_ROLES

BARAKAT_ROLES = list(PERSONA_PRESET_ROLES)


def _provision_barakat_roles():
	from barakat.permissions import BARAKAT_CUSTOM_ROLES, MODULE_ROLE_PERMS

	# Persona roles carry no DocPerms of their own (they exist so the Employee's
	# custom_role_preset Link has something to point at). The custom roles below
	# DO carry perms — granted by _grant_barakat_role_perms.
	#
	# desk_access on the GENERATED module roles must be 1. It is not a security
	# setting — `frappe/handler.py` consults has_desk_access() only to limit upload
	# mimetypes, and the DocPerms are what actually restrict data. What it DOES
	# control is `User.user_type`: `User.set_system_user()` recomputes it on every
	# save as "System User if any role has desk_access else Website User".
	#
	# And login depends on that. The gateway's verify_user_credentials logs into each
	# tenant and accepts ONLY the literal response "Logged In" — Frappe returns
	# "No App" for a Website User. So a persona whose bundle is entirely
	# desk_access=0 roles becomes a Website User and CANNOT LOG IN AT ALL.
	# Measured on osa 2026-07-29: Branch Supervisor, Cashier and HR were locked out.
	for role_name in (*BARAKAT_ROLES, *BARAKAT_CUSTOM_ROLES):
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1 if role_name in MODULE_ROLE_PERMS else 0,
			}
		).insert(ignore_permissions=True)


def _ensure_persona_desk_access():
	"""Repair `desk_access` on generated roles, and `user_type` on persona users.

	Separate from _provision_barakat_roles because that one only touches roles it
	CREATES — a site migrated before this fix already has the 40 generated roles at
	desk_access=0, and its staff already flipped to Website User. Both need repairing
	in place. Idempotent; safe on every migrate.
	"""
	# ALL_ROLE_PERMS, not just the generated MODULE_ROLE_PERMS: `desk_access` is a
	# LOGIN switch, not a permission (proven 2026-07-29 — the DocPerms are what
	# contain a persona), and `set_system_user` needs only ONE held role to carry it.
	# Repairing every role this app owns removes a whole class of fragility instead of
	# relying on each persona happening to hold a generated role.
	#
	# Measured on osa 2026-07-30: 42 of 58 Barakat roles had it, and the 16 without
	# were all hand-written ones (POS Operator, Self Service, Cashier Reader, …). No
	# persona was locked out — the Cashier's 5-role bundle still held 3 with desk
	# access — but a future persona whose bundle happened to be hand-written roles
	# only would have been, and the failure mode is a login that reports "wrong
	# password". `test_every_persona_can_actually_log_in` guards the invariant; this
	# stops it being narrowly true by luck.
	from barakat.permissions import ALL_ROLE_PERMS, PERSONAS

	changed = False
	for role in ALL_ROLE_PERMS:
		if frappe.db.exists("Role", role) and not frappe.db.get_value("Role", role, "desk_access"):
			frappe.db.set_value("Role", role, "desk_access", 1)
			changed = True
	if changed:
		frappe.clear_cache()

	for emp in frappe.get_all(
		"Employee",
		filters={"custom_role_preset": ("in", sorted(PERSONAS)), "user_id": ("is", "set")},
		fields=["user_id"],
	):
		email = (emp.user_id or "").strip()
		if not email or not frappe.db.exists("User", email):
			continue
		# Never touch the owner/Administrator.
		if email == "Administrator":
			continue
		if frappe.db.get_value("User", email, "user_type") != "System User":
			frappe.db.set_value("User", email, "user_type", "System User")


def _grant_barakat_role_perms():
	"""Apply the DocPerms behind every custom `Barakat *` role.

	These roles exist because no native ERPNext role covers the capability without
	also handing out far more — see the per-role notes in `barakat.permissions`.

	Uses frappe.permissions.add_permission, which first copies the doctype's existing
	standard DocPerms into Custom DocPerm (frappe.permissions.setup_custom_perms) before
	adding the new row. That copy is CRITICAL: adding a Custom DocPerm otherwise REPLACES
	the standard perms entirely, which would silently strip `System Manager`'s own access.

	Idempotent: re-adding an existing (role, permlevel) perm is a no-op, and the property
	writes are re-asserted each run — safe to call on every migrate.
	"""
	from frappe.permissions import add_permission, update_permission_property

	from barakat.permissions import ALL_ROLE_PERMS

	for role, doctype_perms in ALL_ROLE_PERMS.items():
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{"doctype": "Role", "role_name": role, "desk_access": 0}
			).insert(ignore_permissions=True)

		for doctype, perms in doctype_perms.items():
			if not frappe.db.exists("DocType", doctype):
				continue  # app shipping that doctype not installed on this site
			add_permission(doctype, role, 0)
			for perm in perms:
				update_permission_property(doctype, role, 0, perm, 1, validate=False)
			frappe.clear_cache(doctype=doctype)


def _revoke_stale_barakat_perms():
	"""Clear permissions a `Barakat *` role no longer declares.

	`_grant_barakat_role_perms` only ever sets a perm to 1 — `update_permission_property`
	has no revoke path of its own. A role that NARROWS between releases therefore keeps
	its old grants on every already-migrated site. That was harmless while roles only
	widened; the 2026-07-29 least-privilege change narrows them, so without this pass the
	whole change silently no-ops wherever the app has been migrated before.

	Only touches roles this app owns (ALL_ROLE_PERMS). Native ERPNext roles and any
	tenant-defined role are never modified.
	"""
	from frappe.permissions import update_permission_property

	from barakat.permissions import ALL_ROLE_PERMS

	all_perms = (
		"read",
		"write",
		"create",
		"delete",
		"submit",
		"cancel",
		"select",
		"report",
		"export",
		"share",
		"print",
		"email",
	)

	for role, doctype_perms in ALL_ROLE_PERMS.items():
		rows = frappe.get_all(
			"Custom DocPerm",
			filters={"role": role, "permlevel": 0},
			fields=["name", "parent"],
		)
		for row in rows:
			wanted = set(doctype_perms.get(row.parent, ()))
			for perm in all_perms:
				if perm in wanted:
					continue
				update_permission_property(row.parent, role, 0, perm, 0, validate=False)
			frappe.clear_cache(doctype=row.parent)


def _grant_owner_payment_mode_perms():
	"""Give `System Manager` full CRUD on Mode of Payment — the owner's delete gap.

	ERPNext ships Mode of Payment with DocPerm rows for Accounts Manager/User and
	HR Manager/User only — no `System Manager` row at all, and no `delete` for
	anyone. Persona staff delete through `Barakat Payment Mode Manager` (see
	barakat.permissions), but an OWNER account holds no persona: it acts under its
	own native roles, so the AP payment-modes page showed the owner a delete
	control (owners bypass the AP/proxy gates) that then 403'd in ERPNext.
	Measured 2026-07-25: has_permission("Mode of Payment", "delete") was False for
	the tenant owner on every site while create was True.

	Granting System Manager the full set closes that asymmetry for owners only —
	no persona bundle contains System Manager, so staff reach is unchanged.

	Uses frappe.permissions.add_permission, which copies the doctype's existing
	standard DocPerms into Custom DocPerm first, so nothing is stripped.
	Idempotent: safe to re-assert on every migrate.
	"""
	from frappe.permissions import add_permission, update_permission_property

	doctype = "Mode of Payment"
	add_permission(doctype, "System Manager", 0)
	for perm in ("read", "write", "create", "delete"):
		update_permission_property(doctype, "System Manager", 0, perm, 1, validate=False)
	frappe.clear_cache(doctype=doctype)


def _ensure_native_report_roles():
	"""Let personas run the ERPNext query reports the AP calls.

	See the long note on `NATIVE_REPORT_MODULES` in `barakat.permissions`: a DocPerm
	does not open a query report. The report's own role allow-list is gate 1, and a
	`Custom Role` is the only override that survives a migrate (standard reports are
	re-synced from their app's JSON, which rewrites the Report's own `Has Role` rows).

	Idempotent, and it rewrites the child table to exactly the computed set so a role
	dropped from `NATIVE_REPORT_MODULES` is removed again rather than lingering.
	"""
	from barakat.permissions import NATIVE_REPORT_MODULES, report_allowed_roles

	for report in NATIVE_REPORT_MODULES:
		if not frappe.db.exists("Report", report):
			continue  # the app shipping that report is not installed here

		native = frappe.get_all("Has Role", filters={"parent": report}, pluck="role")
		wanted = [
			role for role in report_allowed_roles(report, native) if frappe.db.exists("Role", role)
		]
		if not wanted:
			continue

		name = frappe.db.get_value("Custom Role", {"report": report}, "name")
		doc = (
			frappe.get_doc("Custom Role", name)
			if name
			else frappe.get_doc(
				{
					"doctype": "Custom Role",
					"report": report,
					"ref_doctype": frappe.db.get_value("Report", report, "ref_doctype"),
				}
			)
		)
		if sorted(r.role for r in doc.roles) == sorted(wanted):
			continue

		doc.roles = []
		for role in wanted:
			doc.append("roles", {"role": role})
		doc.save(ignore_permissions=True)

	frappe.clear_cache()


def _backfill_persona_roles():
	"""Bring existing persona users onto the allow-list bundle.

	The hook in `barakat.overrides.staff_roles` self-heals on every Employee save,
	but a user whose Employee is never re-saved would keep the roles the old
	everything-minus-deny bundle gave them — including `System Manager` and
	`Script Manager`. This closes that gap on migrate.

	Reuses the hook itself rather than reimplementing the logic, so there is exactly
	one definition of what a persona holds.
	"""
	from barakat.overrides.staff_roles import reassert_persona_roles
	from barakat.permissions import PERSONAS

	employees = frappe.get_all(
		"Employee",
		filters={"custom_role_preset": ("in", sorted(PERSONAS)), "user_id": ("is", "set")},
		pluck="name",
	)
	changes = []
	for name in employees:
		try:
			doc = frappe.get_doc("Employee", name)
			email = (doc.user_id or "").strip()
			before = set(frappe.get_all("Has Role", filters={"parent": email}, pluck="role"))
			reassert_persona_roles(doc)
			after = set(frappe.get_all("Has Role", filters={"parent": email}, pluck="role"))
			if before != after:
				changes.append(
					f"{email} ({doc.custom_role_preset}): "
					f"-{sorted(before - after)} +{sorted(after - before)}"
				)
		except Exception as e:
			frappe.log_error(f"barakat backfill: {name} failed: {e}", "Persona roles")

	# The only record of what an unattended migrate actually changed on this site.
	if changes:
		frappe.log_error(
			f"barakat persona backfill on {frappe.local.site}:\n" + "\n".join(changes),
			"Persona roles backfill",
		)


# Dedicated custom role that carries read+write on ONLY the two singles the admin
# panel's Rounding page needs. NOT a persona (not a custom_role_preset value); it is
# bundled into the Manager persona's ERP role bundle by the proxy
# (proxy-barakat/src/modules/roles/catalog.ts). Keeping it separate from the persona
# roles means the grant below is scoped to exactly the Manager bundle and nothing else.
SETTINGS_MANAGER_ROLE = "Barakat Settings Manager"

# The only doctypes this role may touch. Both are Single doctypes that ship with
# permissions for `System Manager` ONLY. The AP Rounding page reads/writes them under
# the acting user's own session:
#   - Global Defaults.disable_rounded_total  (toggle rounded grand total)
#   - System Settings.rounding_method         (rounding algorithm)
# SECURITY NOTE: System Settings and Global Defaults expose MANY other sensitive
# fields (session/password policy, default company/currency/UOM, number/date formats,
# etc.). Granting write here exposes ALL of those fields to whoever holds this role —
# currently only the Manager persona. This is an accepted, deliberate tradeoff to let
# Managers use the Rounding page without the far broader `System Manager` role.
SETTINGS_MANAGER_DOCTYPES = ("System Settings", "Global Defaults")


def _grant_settings_manager_perms():
	"""Give `Barakat Settings Manager` READ on the two rounding singles.

	Read only — writes go through `barakat.api.settings.set_rounding_settings`.

	Uses frappe.permissions.add_permission, which first copies the doctype's existing
	standard DocPerms into Custom DocPerm (frappe.permissions.setup_custom_perms) before
	adding the new row. That copy is CRITICAL: adding a Custom DocPerm otherwise REPLACES
	the standard perms entirely, which would silently strip `System Manager`'s own
	read/write on these singles. Going through add_permission preserves System Manager.

	Idempotent: re-adding an existing (role, permlevel) perm is a no-op, and the property
	writes are re-asserted each run — safe to call on every migrate.
	"""
	from frappe.permissions import add_permission, update_permission_property

	if not frappe.db.exists("Role", SETTINGS_MANAGER_ROLE):
		frappe.get_doc(
			{"doctype": "Role", "role_name": SETTINGS_MANAGER_ROLE, "desk_access": 1}
		).insert(ignore_permissions=True)

	for doctype in SETTINGS_MANAGER_DOCTYPES:
		# add_permission runs setup_custom_perms(doctype) first → copies the existing
		# System Manager DocPerm into Custom DocPerm, then adds our role's row (perm
		# level 0). Returns None if the row already exists (idempotent).
		add_permission(doctype, SETTINGS_MANAGER_ROLE, 0)
		update_permission_property(doctype, SETTINGS_MANAGER_ROLE, 0, "read", 1, validate=False)
		# READ only. Write is explicitly re-asserted to 0 on every migrate rather than
		# merely omitted, so sites that already granted it correct themselves without a
		# patch. DocPerms are per-doctype, so write here would also hand a Manager
		# session policy, password policy and every other global setting — see New-C in
		# the 2026-07-21 review. The Rounding page's two fields are written instead by
		# barakat.api.settings.set_rounding_settings, elevated and role-checked.
		update_permission_property(doctype, SETTINGS_MANAGER_ROLE, 0, "write", 0, validate=False)
		frappe.clear_cache(doctype=doctype)


# Loyalty Program ships from ERPNext with a SINGLE permission row: `System Manager`
# only (read/write/create/delete). Every other role — including all the functional
# manager roles in the Manager persona bundle (Accounts/Sales/Stock Manager, etc.) —
# has ZERO access, so listing programs raises
#   frappe.exceptions.PermissionError: Insufficient Permission for Loyalty Program
# (a doctype-level select-permission failure, NOT a record/user-permission filter).
#
# The admin panel's Loyalty Programs page ("برامج الولاء") reads Loyalty Program under
# the acting user's own session (proxy → GET /api/loyalty/programs → frappe.client.get_list),
# so a Manager persona — which deliberately does NOT hold `System Manager` (owner-bypass)
# — gets a 403 and the page fails to load. Same class as the Rounding-page block that
# `_grant_settings_manager_perms` fixes for System Settings / Global Defaults.
#
# Fix: grant the Manager-only `Barakat Settings Manager` role read+write+create+delete
# on Loyalty Program. That role is bundled ONLY into the Manager persona
# (proxy-barakat/src/modules/roles/catalog.ts) and is already assigned to existing
# Manager users, so this unblocks them immediately WITHOUT handing out `System Manager`
# and with ZERO blast radius to native ERPNext roles (unlike granting to Sales User /
# Accounts User, which ~70–100 users hold). Manager persona is `customers: write`, so it
# needs full CRUD (create/edit/delete programs), not just read.
#
# NOTE (scope): the Loyalty tab is gated by the `customers` module, which Branch
# Supervisor (write), Cashier (write) and Accountant (read) also have — they hit the
# SAME 403. They are NOT fixed here: none of them holds a dedicated persona-scoped role
# (their bundles are broad native roles like Sales User/Accounts User), so unblocking
# them cleanly requires new dedicated roles bundled into those personas + a back-fill for
# existing users. That is a larger change left for a follow-up decision.
LOYALTY_MANAGER_DOCTYPE = "Loyalty Program"


def _grant_loyalty_manager_perms():
	"""Give the Manager-only `Barakat Settings Manager` role CRUD on Loyalty Program.

	Uses frappe.permissions.add_permission, which copies the doctype's existing standard
	DocPerms into Custom DocPerm (setup_custom_perms) BEFORE adding our row — critical so
	`System Manager`'s own perms are preserved rather than silently replaced.

	Idempotent: re-adding an existing (role, permlevel) perm is a no-op and the property
	writes are re-asserted each run — safe to call on every migrate. Guarded to run only
	when the Loyalty Program doctype exists (it ships with erpnext; the guard is defensive).
	"""
	from frappe.permissions import add_permission, update_permission_property

	if not frappe.db.exists("DocType", LOYALTY_MANAGER_DOCTYPE):
		return  # erpnext not installed / doctype absent — nothing to grant

	if not frappe.db.exists("Role", SETTINGS_MANAGER_ROLE):
		frappe.get_doc(
			{"doctype": "Role", "role_name": SETTINGS_MANAGER_ROLE, "desk_access": 1}
		).insert(ignore_permissions=True)

	add_permission(LOYALTY_MANAGER_DOCTYPE, SETTINGS_MANAGER_ROLE, 0)
	for perm in ("read", "write", "create", "delete"):
		update_permission_property(
			LOYALTY_MANAGER_DOCTYPE, SETTINGS_MANAGER_ROLE, 0, perm, 1, validate=False
		)
	frappe.clear_cache(doctype=LOYALTY_MANAGER_DOCTYPE)


# Dedicated custom role that lets a Manager persona create/edit staff WITHOUT handing
# out the far broader `System Manager` role. Same rationale as `SETTINGS_MANAGER_ROLE`
# above. The Manager creates staff by writing these doctypes DIRECTLY (no service
# account), so the role must grant create+write on EVERY doctype the staff-create flow
# touches — the ERPNext `User` + password, the `Employee` record, its `Designation`,
# and (when a salary is set) the `Salary Structure Assignment`. Granting only `User`
# makes staff creation 403 on the very first `Employee` insert.
#
# The role name is defined in `barakat.permissions` (single source of truth, shared
# with the persona bundles and the preset guard). NOTE: a former `User Permission`
# grant here was dropped 2026-07-22 — nothing in barakat/proxy/AP ever writes a User
# Permission, and the grant let the role delete the tenant-boundary rows. Existing
# sites are cleaned by patches/revoke_staff_manager_user_permission_perm.py. See
# docs/superpowers/specs/2026-07-22-close-internal-escalation-design.md.
from barakat.permissions import STAFF_MANAGER_ROLE

# doctype -> perms the role needs. Ordered by the create flow: Employee first (the
# insert that used to 403), then the login and salary docs.
STAFF_MANAGER_PERMS = {
	"User": ("read", "write", "create"),
	"Employee": ("read", "write", "create"),
	"Designation": ("read", "write", "create"),
	"Salary Structure Assignment": ("read", "write", "create", "submit", "cancel"),
}


def _grant_staff_manager_perms():
	"""Give `Barakat Staff Manager` create+write on every doctype the staff-create flow writes.

	Uses frappe.permissions.add_permission, which first copies the doctype's existing
	standard DocPerms into Custom DocPerm (frappe.permissions.setup_custom_perms) before
	adding the new row. That copy is CRITICAL: adding a Custom DocPerm otherwise REPLACES
	the standard perms entirely, which would silently strip `System Manager`'s (and HR's)
	own perms on that doctype. Going through add_permission preserves them.

	Idempotent: re-adding an existing (role, permlevel) perm is a no-op, and the property
	writes are re-asserted each run — safe to call on every migrate.
	"""
	from frappe.permissions import add_permission, update_permission_property

	if not frappe.db.exists("Role", STAFF_MANAGER_ROLE):
		frappe.get_doc(
			{"doctype": "Role", "role_name": STAFF_MANAGER_ROLE, "desk_access": 1}
		).insert(ignore_permissions=True)

	# add_permission runs setup_custom_perms(doctype) first → copies the existing
	# standard DocPerms (System Manager, HR, …) into Custom DocPerm, then adds our
	# role's row (perm level 0). Returns None if the row already exists (idempotent).
	for doctype, perms in STAFF_MANAGER_PERMS.items():
		add_permission(doctype, STAFF_MANAGER_ROLE, 0)
		for perm in perms:
			update_permission_property(doctype, STAFF_MANAGER_ROLE, 0, perm, 1, validate=False)
		frappe.clear_cache(doctype=doctype)


def _lock_create_user_permission_field():
	"""Grey out `Employee.create_user_permission` for everyone but the owner.

	The server-side refusal lives in `overrides.staff_roles.guard_user_permission_flag`
	— this is only so a Manager is not shown a checkbox they will be refused for
	clicking. A Property Setter cannot express "read-only for these roles", but
	`read_only_depends_on` is evaluated in the browser with `frappe.user_roles` in
	scope, which says the same thing for the one caller it needs to.

	Deliberately NOT a permlevel bump. Raising the field to permlevel 1 would also hide
	it from the Manager's Employee FORM READ, and `User.roles` is already the standing
	proof of how quietly a permlevel-1 field is dropped from a save (see the note in
	overrides/staff_roles.py). This changes what the desk offers, nothing else.

	make_property_setter upserts, so re-asserting on every migrate is idempotent.
	"""
	frappe.make_property_setter(
		{
			"doctype": "Employee",
			"fieldname": "create_user_permission",
			"property": "read_only_depends_on",
			"value": 'eval:!frappe.user_roles.includes("System Manager")',
			# Matches the DocField's own fieldtype for this property (Code), so the
			# Property Setter is applied verbatim rather than cast.
			"property_type": "Code",
		}
	)
	frappe.clear_cache(doctype="Employee")


def _relax_demo_company_user_perm():
	"""Ignore user permissions on `Global Defaults.demo_company`.

	Global Defaults ships a `demo_company` Link(Company) field with
	`ignore_user_permissions = 0`. A company-restricted user (e.g. a Manager holding
	`Barakat Settings Manager`) then gets a 403 reading Global Defaults — the record is
	"linked to Company … field Demo Company" that their User Permission doesn't allow.
	The sibling `default_company` field already ships with ignore_user_permissions = 1;
	this mirrors that so the AP Rounding page can read Global Defaults.

	Applied via a Property Setter (make_property_setter upserts, so it's idempotent and
	safe to re-assert on every migrate). Guarded to run only when the field exists —
	some sites (no demo data / older schema) may not have `demo_company`.
	"""
	field = frappe.get_meta("Global Defaults").get_field("demo_company")
	if not field:
		return  # field absent on this site — nothing to relax

	frappe.make_property_setter(
		{
			"doctype": "Global Defaults",
			"fieldname": "demo_company",
			"property": "ignore_user_permissions",
			"value": 1,
			"property_type": "Check",
		}
	)
	frappe.clear_cache(doctype="Global Defaults")
