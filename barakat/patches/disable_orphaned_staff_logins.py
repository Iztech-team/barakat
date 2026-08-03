"""Disable logins left behind by a staff email that was changed, not renamed.

Until the rename fix, correcting a staff member's login email in the admin panel
did not change their login. `staff.update` created a SECOND User for the new
address and repointed `Employee.user_id` at it, leaving the old one fully intact:
same password, same persona roles (the roles hook resolves its target from
`user_id`, which now names the new address), and same Company User Permission
(`reassert_company_user_permission` is add-only by design).

That leftover is a working key. Login does not consult the master's records at
all — `iztechvalley_gateway.api.tenant_picker.verify_user_credentials` tries the
submitted credentials against each site and returns the ones that accept them —
so the abandoned account still opens the shop it was abandoned in. The case that
motivated the fix is the one that makes this worst: a manager retypes the email
when someone leaves, believing they are handing the job over.

Fixing the code does not close the accounts already opened. This does.

DISABLES rather than deletes. Such a User may own historical documents, and
`enabled = 0` is enough for Frappe to refuse the login while staying reversible by
anyone who finds a legitimate account in the log below.

Scope, deliberately narrow — a User is swept only when ALL of these hold:
  * enabled
  * not Administrator / Guest
  * no Employee anywhere on the site points at it via `user_id`
  * it holds no owner role (System Manager / Administrator)
  * it holds at least one role granted by a persona bundle

The last rule is what keeps this from reaching past its own mess. A site's Users
include integration accounts and hand-made desk logins that never had an Employee
and never should. Only an account carrying persona roles was provisioned by the
staff flow, so only that account can be one of ours.

Every account is logged with its roles and last login BEFORE it is touched, so
there is a record to answer from if someone asks what happened to a login.

Idempotent: a disabled user is not enabled, so a second run sweeps nothing.
"""

import frappe

from barakat.permissions import PERSONA_ROLE_BUNDLES

OWNER_ROLES = {"System Manager", "Administrator"}
SYSTEM_USERS = {"Administrator", "Guest"}


def _persona_granted_roles():
	"""Every role any persona bundle grants, as one set."""
	roles = set()
	for bundle in PERSONA_ROLE_BUNDLES.values():
		roles.update(bundle)
	return roles


def execute():
	if not frappe.db.table_exists("Employee"):
		return  # not a shop site: no staff flow ever ran here

	# get_all, not get_list: this must see EVERY employee on the site regardless of
	# who is running the migration, or a user still linked to an invisible Employee
	# would look orphaned and be disabled.
	linked = {email for email in frappe.get_all("Employee", pluck="user_id") if email}
	persona_roles = _persona_granted_roles()
	if not persona_roles:
		return  # site not provisioned; nothing here was created by the staff flow

	candidates = frappe.get_all(
		"User",
		filters={"enabled": 1, "name": ("not in", list(SYSTEM_USERS))},
		fields=["name", "last_login"],
	)

	for user in candidates:
		if user.name in linked:
			continue

		held = set(frappe.get_roles(user.name))
		if held & OWNER_ROLES:
			continue
		if not (held & persona_roles):
			# Never provisioned by the staff flow — an integration account or a
			# hand-made desk login. Not ours to disable.
			continue

		# Logged before the write, so the record survives even if the disable fails.
		frappe.logger("barakat").warning(
			"disabling orphaned staff login on %s: %s (last_login=%s, roles=%s)"
			% (frappe.local.site, user.name, user.last_login, sorted(held))
		)
		frappe.db.set_value("User", user.name, "enabled", 0, update_modified=False)
		frappe.clear_document_cache("User", user.name)
