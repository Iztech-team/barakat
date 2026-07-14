"""Re-assert the AP persona ERPNext role bundle on staff Users.

When a non-System-Manager Manager creates staff via the Admin Panel, ERPNext
silently drops the User `roles` child table writes because that table is
permlevel 1 (writable only by System Manager). New staff end up with only the
`Employee` role. This hook re-applies the broad role bundle to the linked User
with elevated permissions (the same elevated path ERPNext itself uses to append
`Employee`), bypassing the permlevel-1 gate WITHOUT granting the Manager any new
permission.

Security notes:
- Per the tenant owner's explicit request, the bundle now includes EVERY enabled
  site role EXCEPT `Administrator` — INCLUDING `System Manager`. Staff personas
  therefore intentionally receive full ERPNext admin. See the RISK note on
  BROAD_ERP_BUNDLE below.
- Only `Administrator` is protected: accounts already holding `Administrator` are
  never modified, and the bundle can never contain it.
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

# The broad ERPNext role bundle every AP persona shares. Must match the proxy
# BROAD_ERP_BUNDLE. This is the FULL set of non-disabled roles on the site
# EXCEPT `Administrator` (and the Frappe-managed base roles Guest / All), so a
# persona session never 403s an action the admin panel allows. The list is
# hard-coded (not queried at runtime) so it stays static and reviewable.
# Resolved from pos2 on 2026-07-14.
#
# RISK — the following included roles grant full-admin / code-execution reach and
# may want removing (flagged to the tenant owner, who explicitly requested them):
#   - "System Manager": full ERPNext admin (all doctypes, users, permissions,
#     permlevel-1 role writes). Owner-equivalent; included per explicit request.
#   - "Script Manager": can create Server Scripts (arbitrary Python) = escalation.
#   - "Report Manager": can create Query/Script Reports (embedded code).
#   - "Baraka Owner": tenant owner role; owner-adjacent by name.
BROAD_ERP_BUNDLE = [
	"Academics User",
	"Accountant",
	"Accounts Manager",
	"Accounts User",
	"Analytics",
	"Auditor",
	"Baraka Branch",
	"Baraka Owner",
	"Barakat Settings Manager",
	"Barakat Staff Manager",
	"Branch Supervisor",
	"Cashier",
	"Customer",
	"Dashboard Manager",
	"Delivery Manager",
	"Delivery User",
	"Desk User",
	"Employee",
	"Employee Self Service",
	"Expense Approver",
	"Fleet Manager",
	"Fulfillment User",
	"HR",
	"HR Manager",
	"HR User",
	"Inbox User",
	"Interviewer",
	"Inventory Keeper",
	"Item Manager",
	"Knowledge Base Contributor",
	"Knowledge Base Editor",
	"Leave Approver",
	"Maintenance Manager",
	"Maintenance User",
	"Manager",
	"Manufacturing Manager",
	"Manufacturing User",
	"Marketing Manager",
	"Newsletter Manager",
	"Prepared Report User",
	"Projects Manager",
	"Projects User",
	"Purchase Manager",
	"Purchase Master Manager",
	"Purchase User",
	"Quality Manager",
	"Report Manager",
	"Sales Manager",
	"Sales Master Manager",
	"Sales User",
	"Script Manager",
	"Stock Manager",
	"Stock User",
	"Supplier",
	"Support Team",
	"System Manager",
	"Translator",
	"Website Manager",
	"Workspace Manager",
]

# `Administrator` is the only untouchable account: it must never be granted by
# this hook, and accounts already holding it are never modified. (System Manager
# is intentionally part of the bundle per the tenant owner's request, so it is
# NOT protected here.)
PROTECTED_ROLES = frozenset({"Administrator"})

# Safety assertion: the bundle must never contain Administrator.
assert "Administrator" not in set(BROAD_ERP_BUNDLE), (
	"BROAD_ERP_BUNDLE must not contain Administrator"
)


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

	# Add only the bundle roles the user is actually missing.
	missing = [role for role in BROAD_ERP_BUNDLE if role not in existing_roles]
	if missing:
		# add_roles saves the User with ignore_permissions internally, so this
		# bypasses the permlevel-1 gate without needing the caller's permission.
		user.add_roles(*missing)
