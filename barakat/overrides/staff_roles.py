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
# BROAD_ERP_BUNDLE. Intentionally excludes System Manager / Administrator.
BROAD_ERP_BUNDLE = (
	"Manager",
	"Accounts Manager",
	"Sales Master Manager",
	"Sales Manager",
	"Stock Manager",
	"Item Manager",
	"Purchase Manager",
	"Purchase Master Manager",
	"HR Manager",
	"Barakat Settings Manager",
	"Barakat Staff Manager",
	"Accounts User",
	"Sales User",
	"Stock User",
	"Purchase User",
	"HR User",
)

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
