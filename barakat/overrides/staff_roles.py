"""Apply the AP persona ERPNext role bundle to staff Users.

When a non-System-Manager Manager creates staff via the Admin Panel, ERPNext
silently drops the User `roles` child table writes because that table is
permlevel 1 (writable only by System Manager). New staff end up with only the
`Employee` role. This hook applies the persona's role bundle to the linked User
with elevated permissions (the same elevated path ERPNext itself uses to append
`Employee`), bypassing the permlevel-1 gate WITHOUT granting the Manager any new
permission.

The bundles themselves live in `barakat.permissions` — see that module for what
each persona gets and why. The proxy does not send roles: it cannot know which
roles a given site defines, and a list duplicated across two repos only drifts.

Security notes:
- Each persona receives an explicit **allow-list** of roles. `System Manager`,
  `Script Manager` and `Report Manager` are in no bundle and are actively removed
  from persona users by this hook.
- `Administrator` is protected: accounts already holding it are never modified.
- The bundle is intersected with the roles that exist on the site, so a site
  missing one of them is skipped rather than raising LinkValidationError. That
  failure once broke staff creation entirely on every site lacking `Baraka
  Branch`; see the history note below.

History: this file previously granted every enabled role on the site except a
five-name deny-list, on the tenant owner's explicit request, with the admin
panel's client-side matrix as the only constraint. On BOM that produced HR clerks
holding 57 roles including `Script Manager`. Reversed 2026-07-19 — see
`proxy-barakat/docs/superpowers/specs/2026-07-19-real-roles-and-permissions-design.md`.
"""

import frappe
from frappe import _

from barakat.permissions import (
	FORBIDDEN_ROLES,
	PERSONAS,
	PRESERVED_ROLES,
	bundle_for,
	may_assign_preset,
)

# `Administrator` is the only untouchable account: accounts holding it are never
# modified by this hook.
PROTECTED_ROLES = frozenset({"Administrator"})

assert "Administrator" in FORBIDDEN_ROLES, "Administrator must never be grantable"


def persona_role_bundle(persona):
	"""The persona's roles, restricted to those that exist on THIS site.

	Queried per call rather than cached: roles change when an app is installed or a
	tenant adds one, and a stale bundle is exactly the failure this replaced.
	"""
	wanted = bundle_for(persona)
	if not wanted:
		return []
	existing = set(
		frappe.get_all("Role", filters={"name": ("in", list(wanted)), "disabled": 0}, pluck="name")
	)
	return [role for role in wanted if role in existing]


def reassert_company_user_permission(doc, method=None):
	"""Ensure the Employee's linked user keeps their company User Permission.

	This is the tenant boundary. ERPNext creates it in
	`erpnext/setup/doctype/employee/employee.py::update_user_permissions`, but only
	when `user_id` or the `create_user_permission` checkbox CHANGES — and it DELETES
	both the Employee and the Company permission when that box is unticked. The
	checkbox's description says only "This will restrict user access to other employee
	records", so unticking it silently unscopes the user from every other company's
	data. Measured on BOM: a scoped user sees 0 of 190 POS invoices; an unscoped one
	sees all of them.

	Re-asserting here splits the two concerns the checkbox conflates: it keeps owning
	the own-employee-record restriction, we own the tenant restriction.

	ADD-ONLY. Never removes a permission: staff may legitimately span shops, and a
	second company granted by hand must survive.
	"""
	from frappe.permissions import add_user_permission

	preset = (doc.custom_role_preset or "").strip()
	if preset not in PERSONAS:
		return

	email = (doc.user_id or "").strip()
	company = (doc.company or "").strip()
	if not email or not company or email == "Administrator":
		return
	if not frappe.db.exists("User", email):
		return

	if frappe.db.exists(
		"User Permission", {"user": email, "allow": "Company", "for_value": company}
	):
		return

	add_user_permission("Company", company, email)


def guard_role_preset(doc, method=None):
	"""Reject setting/changing an Employee's persona preset unless the caller may.

	Wired on Employee `validate`. The stamp drives the whole role bundle, so only a
	staff-admin (the Manager persona) or the owner/System Manager may assign it. HR,
	which no longer carries the staff-admin role, is blocked here even though native
	`HR Manager` still gives it generic Employee write for payroll. Fires only when
	the preset actually changes, so a salary/attendance edit is never affected.
	"""
	new_preset = (doc.custom_role_preset or "").strip()
	if not new_preset:
		return
	if not doc.has_value_changed("custom_role_preset"):
		return
	if may_assign_preset(
		frappe.get_roles(frappe.session.user),
		is_administrator=frappe.session.user == "Administrator",
		is_system_context=bool(
			frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch
		),
	):
		return
	frappe.throw(
		_("Only a manager can assign or change a staff member's role."),
		frappe.PermissionError,
	)


def reassert_persona_roles(doc, method=None):
	"""Apply the persona role bundle to the Employee's linked User.

	Wired on Employee after_insert and on_update. No-op unless the Employee has
	both a recognised persona preset and a linked, existing, non-owner User.

	Self-healing in both directions: missing bundle roles are added, and any
	forbidden role the user picked up under the old everything-minus-deny model is
	removed. Users whose Employee is never re-saved are handled by the backfill in
	`barakat.setup.install`.
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

	# No persona keeps the "Employee = own record" User Permission. It scopes the
	# user to their own Employee across ALL doctypes, so it silently hides other
	# people's shifts / attendance / salary too. Company-scoping User Permissions
	# are left alone — those are the tenant boundary, not a role gate.
	for up_name in frappe.get_all(
		"User Permission", filters={"user": email, "allow": "Employee"}, pluck="name"
	):
		frappe.delete_doc("User Permission", up_name, ignore_permissions=True, force=True)

	# add_roles/remove_roles save the User with ignore_permissions internally, so
	# this bypasses the permlevel-1 gate without needing the caller's permission.
	# The bundle is AUTHORITATIVE for a persona user: the roles child table is
	# rewritten to exactly the bundle, plus any PRESERVED_ROLES already present.
	#
	# Adding only what is missing is not enough — a user created under the old
	# everything-minus-deny model keeps ~50 unrelated roles (Fleet Manager, Projects
	# Manager, Academics User…) that no persona should ever have carried. Stripping
	# just System/Script/Report Manager would leave them looking fixed while still
	# far outside least privilege.
	target = set(persona_role_bundle(preset)) | (existing_roles & PRESERVED_ROLES)
	if target == existing_roles:
		return

	# Write the child table directly and save with ignore_permissions, rather than
	# via User.add_roles / remove_roles.
	#
	# `User.roles` is permlevel 1, writable only by System Manager. add_roles() ends
	# in a plain self.save(), so for any caller without System Manager Frappe SILENTLY
	# DROPS the roles rows — no error, the staff member is simply created with only
	# `Employee` and cannot use the system. That went unnoticed while this file
	# granted every persona System Manager: the Manager or HR creating the staff
	# member happened to have permlevel-1 access themselves. Removing System Manager
	# from the bundles broke that assumption, and the first HR-created Cashier came
	# out with exactly one role.
	#
	# ignore_permissions=True is safe here precisely because it is not the caller's
	# permission being used: the role list is computed from the persona, never from
	# request input, so this grants the acting Manager/HR no reach of their own.
	user.set("roles", [])
	for role in sorted(target):
		user.append("roles", {"role": role})
	user.save(ignore_permissions=True)
