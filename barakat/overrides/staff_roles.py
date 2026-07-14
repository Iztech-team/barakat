"""Re-assert the AP persona ERPNext role bundle on staff Users.

When a non-System-Manager Manager creates staff via the Admin Panel, ERPNext
silently drops the User `roles` child table writes because that table is
permlevel 1 (writable only by System Manager). New staff end up with only the
`Employee` role. This hook re-applies the broad role bundle to the linked User
with elevated permissions (the same elevated path ERPNext itself uses to append
`Employee`), bypassing the permlevel-1 gate WITHOUT granting the Manager any new
permission.

Security notes:
- The bundle intentionally excludes `System Manager` / `Administrator`, so this
  is not a self-escalation path.
- Owner/admin accounts (already holding `System Manager` or `Administrator`) are
  never modified.
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

# The broad ERPNext role bundle every AP persona shares. Must match the proxy
# BROAD_ERP_BUNDLE. This is the FULL set of non-disabled roles on the site
# EXCEPT the owner/escalation roles System Manager / Administrator (and the
# Frappe-managed base roles Guest / All), so a persona session never 403s an
# action the admin panel allows. The list is hard-coded (not queried at runtime)
# so it stays static and reviewable. Resolved from pos2 on 2026-07-14.
#
# RISK — the following included roles grant code-execution / owner-adjacent
# reach and may want removing (flagged to the tenant owner):
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
	"Translator",
	"Website Manager",
	"Workspace Manager",
]

# Roles that must never be granted by this hook and mark an owner/admin account
# that must never be touched.
PROTECTED_ROLES = frozenset({"System Manager", "Administrator"})

# Safety assertion: the bundle must never contain a protected role.
assert not (set(BROAD_ERP_BUNDLE) & PROTECTED_ROLES), (
	"BROAD_ERP_BUNDLE must not contain System Manager / Administrator"
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

	# Add only the bundle roles the user is actually missing. If nothing is
	# missing, short-circuit to avoid a wasted save (and any re-fire).
	missing = [role for role in BROAD_ERP_BUNDLE if role not in existing_roles]
	if not missing:
		return

	# add_roles saves the User with ignore_permissions internally, so this
	# bypasses the permlevel-1 gate without needing the caller's permission.
	user.add_roles(*missing)
