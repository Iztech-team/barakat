"""Re-assert the AP persona ERPNext role bundle on staff Users.

When a non-System-Manager Manager creates staff via the Admin Panel, ERPNext
silently drops the User `roles` child table writes because that table is
permlevel 1 (writable only by System Manager). New staff end up with only the
`Employee` role. This hook re-applies the broad role bundle to the linked User
with elevated permissions (the same elevated path ERPNext itself uses to append
`Employee`), bypassing the permlevel-1 gate WITHOUT granting the Manager any new
permission.

This is the single source of truth for which ERPNext roles a staff user holds. The
proxy does not send roles — it cannot know which roles a given site defines, and a
list duplicated across two repos only drifts. The bundle is resolved from the site's
own Role table at runtime; see ROLE_DENY_LIST.

Security notes:
- Per the tenant owner's explicit request, staff receive EVERY enabled site role
  except ROLE_DENY_LIST — INCLUDING `System Manager`. Staff personas therefore
  intentionally receive full ERPNext admin, and the admin panel's module matrix is
  what actually constrains them. See the RISK note on ROLE_DENY_LIST below.
- `Administrator` is protected: accounts already holding it are never modified, and
  it can never be granted.
- Only missing roles are added; nothing is ever removed.
"""

import frappe

# Personas (Employee.custom_role_preset) that trigger the re-assert. Must match
# the AP personas.
PERSONAS = frozenset(
	{
		"Manager",
		"Branch Supervisor",
		"Cashier",
		"Accountant",
		"Inventory Keeper",
		"HR",
	}
)

# Personas that manage/oversee people and must see ALL staff — so they must NOT
# carry the "Employee = own record" User Permission. That lock scopes a user to
# their own Employee, and because shifts / attendance / salary all link to
# Employee, it silently hides other people's records too (e.g. a Branch
# Supervisor seeing only their own shifts). Cashier / Inventory Keeper are NOT
# here — they legitimately keep the own-record lock.
SEE_ALL_PERSONAS = frozenset(
	{
		"Manager",
		"Branch Supervisor",
		"Accountant",
		"HR",
	}
)

# Roles no persona may ever receive. Everything else enabled on the site IS granted
# (the tenant owner's rule: every staff user gets every role, and the admin panel's
# module matrix — not ERPNext roles — decides what they can see and do).
#
# The bundle is resolved from the site at runtime, NOT hard-coded. A hard-coded list
# is necessarily a snapshot of ONE site: the previous list was enumerated from pos2
# and carried its site-local "Baraka Branch" / "Baraka Owner" roles, so on every site
# lacking them (qa-test, fatima) add_roles() raised LinkValidationError and no staff
# could be created at all. Sites legitimately differ — apps come and go, tenants add
# their own roles — so the only correct answer is to ask the site.
#
# RISK — roles granted here that carry full-admin / code-execution reach (flagged to
# the tenant owner, who explicitly requested them):
#   - "System Manager": full ERPNext admin (all doctypes, users, permissions,
#     permlevel-1 role writes). Owner-equivalent; granted per explicit request.
#   - "Script Manager": can create Server Scripts (arbitrary Python) = escalation.
#   - "Report Manager": can create Query/Script Reports (embedded code).
ROLE_DENY_LIST = frozenset(
	{
		# Frappe manages these itself; they are not grantable persona roles.
		"Administrator",
		"All",
		"Guest",
		# Tenant-owner roles. These exist only on some sites (e.g. pos2) and must
		# never land on staff — granting "every role" would otherwise make every
		# cashier a tenant owner on the sites that define them.
		"Baraka Owner",
		"Baraka Branch",
	}
)

# `Administrator` is the only untouchable account: it must never be granted by
# this hook, and accounts already holding it are never modified. (System Manager
# is intentionally granted per the tenant owner's request, so it is NOT protected.)
PROTECTED_ROLES = frozenset({"Administrator"})

assert "Administrator" in ROLE_DENY_LIST, "Administrator must never be grantable"


def persona_role_bundle():
	"""Every enabled role on THIS site except the deny-list.

	Queried per call rather than cached: roles change when an app is installed or a
	tenant adds one, and a stale bundle is exactly the failure this replaced.
	"""
	return [
		role
		for role in frappe.get_all("Role", filters={"disabled": 0}, pluck="name")
		if role not in ROLE_DENY_LIST
	]


def reassert_persona_roles(doc, method=None):
	"""Re-apply the persona role bundle to the Employee's linked User.

	Wired on Employee after_insert and on_update. No-op unless the Employee has
	both a recognised persona preset and a linked, existing, non-owner User that
	is actually missing bundle roles.
	"""
	preset = (doc.custom_role_preset or "").strip()
	if preset not in PERSONAS:
		return

	email = (doc.user_id or "").strip()
	if not email or not frappe.db.exists("User", email):
		return

	user = frappe.get_doc("User", email)

	# Never modify owner/admin accounts.
	existing_roles = {row.role for row in (user.get("roles") or [])}
	if existing_roles & PROTECTED_ROLES:
		return

	# See-all personas must never keep the "Employee = own record" User Permission
	# — it hides other people's shifts / attendance / salary (all link to Employee).
	# Strip any that exist (ERPNext or a prior persona can leave one behind).
	if preset in SEE_ALL_PERSONAS:
		for up_name in frappe.get_all(
			"User Permission", filters={"user": email, "allow": "Employee"}, pluck="name"
		):
			frappe.delete_doc("User Permission", up_name, ignore_permissions=True, force=True)

	# Add only the roles the user is actually missing. Every role here came from this
	# site's own Role table, so add_roles() can never hit an unknown link.
	missing = [role for role in persona_role_bundle() if role not in existing_roles]
	if missing:
		# add_roles saves the User with ignore_permissions internally, so this
		# bypasses the permlevel-1 gate without needing the caller's permission.
		user.add_roles(*missing)
