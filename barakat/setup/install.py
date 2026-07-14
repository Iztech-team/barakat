import frappe


def after_install():
	for fn in [
		_enable_negative_stock,
		_set_session_expiry,
		_set_pos_invoice_type,
		_set_commercial_rounding,
		_create_default_customer,
		_provision_barakat_roles,
		_grant_settings_manager_perms,
		_grant_loyalty_manager_perms,
		_grant_staff_manager_perms,
		_relax_demo_company_user_perm,
		_provision_loyalty_payment_methods,
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
	#
	# _provision_barakat_roles + _grant_settings_manager_perms run here too so
	# EXISTING tenants (installed before this change) pick up the new
	# `Barakat Settings Manager` role and its System Settings / Global Defaults
	# permissions on the next `bench migrate` — no reinstall needed, and the
	# grant survives future migrations (it's re-asserted, idempotently, each run).
	for fn in [
		_create_misc_item,
		_fix_stock_adjustment_accounts,
		_provision_barakat_roles,
		_grant_settings_manager_perms,
		_grant_loyalty_manager_perms,
		_grant_staff_manager_perms,
		_relax_demo_company_user_perm,
		_provision_loyalty_payment_methods,
	]:
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
	"""Give `Barakat Settings Manager` read+write on the two rounding singles.

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
		update_permission_property(doctype, SETTINGS_MANAGER_ROLE, 0, "write", 1, validate=False)
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
# the branch/company `User Permission` scoping rows, and (when a salary is set) the
# `Salary Structure Assignment`. Granting only `User` makes staff creation 403 on the
# very first `Employee` insert.
STAFF_MANAGER_ROLE = "Barakat Staff Manager"

# doctype -> perms the role needs. Ordered by the create flow: Employee first (the
# insert that used to 403), then the login + scoping docs.
STAFF_MANAGER_PERMS = {
	"User": ("read", "write", "create"),
	"Employee": ("read", "write", "create"),
	"Designation": ("read", "write", "create"),
	"User Permission": ("read", "write", "create", "delete"),
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


LOYALTY_MODE_PREFIX = "Loyalty Points"


def _loyalty_mode_name(company: str) -> str:
	return f"{LOYALTY_MODE_PREFIX} - {company}"


def _ensure_loyalty_liability_account(company: str) -> str | None:
	"""Dedicated Liability account 'Loyalty Points Payable - <abbr>' for the company.
	Redeemed loyalty points are money owed to customers (a liability), so the Mode of
	Payment must settle against a Balance-Sheet liability account -- NOT a P&L expense
	account (an expense account requires a cost center and breaks POS shift-close).
	Returns the account name, or None if the company has no abbr / no Liability parent."""
	abbr = frappe.db.get_value("Company", company, "abbr")
	if not abbr:
		return None
	acct_name = f"Loyalty Points Payable - {abbr}"
	if frappe.db.exists("Account", acct_name):
		return acct_name
	# Parent = the company's Current Liabilities group, falling back to the top-level
	# Liabilities group (root_type Liability). root_type is inherited from the parent,
	# which is what actually keeps the account on the Balance Sheet.
	parent = frappe.db.get_value(
		"Account",
		{"company": company, "account_name": "Current Liabilities", "root_type": "Liability", "is_group": 1},
		"name",
	) or frappe.db.get_value(
		"Account",
		{"company": company, "root_type": "Liability", "is_group": 1, "parent_account": ["is", "not set"]},
		"name",
	) or frappe.db.get_value(
		"Account", {"company": company, "root_type": "Liability", "is_group": 1}, "name"
	)
	if not parent:
		return None
	doc = frappe.get_doc({
		"doctype": "Account",
		"account_name": "Loyalty Points Payable",
		"company": company,
		"parent_account": parent,
		"root_type": "Liability",
		"report_type": "Balance Sheet",
		"is_group": 0,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def _provision_loyalty_payment_for_company(company: str) -> None:
	"""Idempotently ensure the 'Loyalty Points - <company>' Mode of Payment exists
	(type General, custom_company set, per-company account row) and is on every POS
	Profile of that company. Safe to re-run."""
	acct = _ensure_loyalty_liability_account(company)
	mode_name = _loyalty_mode_name(company)
	if not frappe.db.exists("Mode of Payment", mode_name):
		mop = frappe.get_doc({
			"doctype": "Mode of Payment",
			"mode_of_payment": mode_name,
			"type": "General",
			"enabled": 1,
		})
		# custom_company is a barakat custom field on Mode of Payment (same convention
		# as the existing "<name> - <company>" modes). Set only if the field exists.
		if mop.meta.has_field("custom_company"):
			mop.custom_company = company
		if acct:
			mop.append("accounts", {"company": company, "default_account": acct})
		mop.insert(ignore_permissions=True)
	else:
		mop = frappe.get_doc("Mode of Payment", mode_name)
		changed = False
		if mop.meta.has_field("custom_company") and mop.custom_company != company:
			mop.custom_company = company; changed = True
		if acct:
			# Migrate an existing row (which may still point at the old expense account)
			# onto the liability account, or add the row if missing.
			row = next((r for r in mop.accounts if r.company == company), None)
			if row is None:
				mop.append("accounts", {"company": company, "default_account": acct}); changed = True
			elif row.default_account != acct:
				row.default_account = acct; changed = True
		if changed:
			mop.save(ignore_permissions=True)

	# Add to each POS Profile of this company (non-default), idempotently.
	for profile_name in frappe.get_all("POS Profile", filters={"company": company}, pluck="name"):
		profile = frappe.get_doc("POS Profile", profile_name)
		if not any(p.mode_of_payment == mode_name for p in profile.payments):
			profile.append("payments", {"mode_of_payment": mode_name, "default": 0})
			profile.save(ignore_permissions=True)


def _provision_loyalty_payment_methods() -> None:
	for company in frappe.get_all("Company", pluck="name"):
		try:
			_provision_loyalty_payment_for_company(company)
		except Exception:
			frappe.log_error(title="barakat: loyalty payment provisioning failed", message=frappe.get_traceback())


def provision_company_loyalty_payment(doc, method):
	"""Company after_insert: create this new shop's Loyalty Points mode immediately."""
	_provision_loyalty_payment_for_company(doc.name)
