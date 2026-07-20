"""Resolve the CURRENT user's POS persona for the desktop app's login gate.

The Electrobun POS restricts which personas may establish a device session
(Manager / Branch Supervisor / owner). The persona lives on the user's Employee
record (`custom_role_preset`), but a Branch Supervisor's role bundle does NOT grant
read on the Employee doctype, so the POS cannot resolve it with a plain client-side
query — the very role we want to allow would come back empty and be wrongly blocked.

This whitelisted method runs under the caller's own session and looks up ONLY the
caller's own Employee (keyed by `frappe.session.user`), using ignore_permissions so
the answer does not depend on whether the persona can read Employee. It exposes no
one else's data — it can only ever report on the logged-in user themselves.
"""

import frappe

# Personas allowed to establish a POS device session. Mirrors the desktop app's
# gate and the `verifyAuthorizedPin` allow-list. Cashier / Accountant / Inventory
# Keeper / HR are intentionally excluded: they operate the till by PIN under a
# Manager's / Branch Supervisor's device session, never their own ERPNext session.
POS_LOGIN_PERSONAS = ("Manager", "Branch Supervisor")

# Roles that mark a tenant owner / administrator, who may always set up a device.
OWNER_ROLES = ("System Manager", "Administrator")


@frappe.whitelist()
def get_my_pos_role() -> dict:
	"""Return the caller's POS persona and whether they may log into the desktop app.

	Shape:
	  {
	    "persona": "<custom_role_preset or ''>",
	    "is_owner": <bool>,          # holds System Manager / Administrator
	    "allowed": <bool>,           # may establish a POS device session
	  }

	`allowed` is true for POS_LOGIN_PERSONAS or any owner/admin. The desktop app
	enforces the gate; this method is the single source of truth it consults.
	"""
	user = frappe.session.user

	# Own roles are always readable for the current user, regardless of doctype perms.
	roles = set(frappe.get_roles(user))
	is_owner = any(r in roles for r in OWNER_ROLES)

	# The caller's own Employee only, by user_id. ignore_permissions is safe here:
	# the filter is pinned to frappe.session.user, so no other record is reachable.
	persona = ""
	rows = frappe.get_all(
		"Employee",
		filters={"user_id": user},
		fields=["custom_role_preset"],
		limit=1,
		ignore_permissions=True,
	)
	if rows:
		persona = (rows[0].get("custom_role_preset") or "").strip()

	allowed = is_owner or persona in POS_LOGIN_PERSONAS
	return {"persona": persona, "is_owner": is_owner, "allowed": allowed}


@frappe.whitelist()
def update_my_profile_name(full_name: str) -> dict:
	"""Rename the CURRENT user's own Employee (and, via ERPNext's Employee.on_update
	-> update_user cascade, their linked User).

	Self-service: a user editing their OWN display name should not require holding
	`Employee` write. Most personas (Cashier, Accountant, Inventory Keeper, Branch
	Supervisor) do NOT — so the admin panel's profile save used to 502 for them.
	This runs under the caller's session but writes only the Employee pinned to
	`frappe.session.user`, with ignore_permissions, so it can never touch anyone
	else's record. (We cannot instead grant the native "Employee Self Service" role:
	the persona bundles deliberately drop the "own record" User Permission, so that
	role would grant write on EVERY Employee, not just the caller's.)
	"""
	name = (full_name or "").strip()
	if not name:
		frappe.throw("Name cannot be blank.")
	user = frappe.session.user
	emp = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not emp:
		frappe.throw("Your account has no staff record, so its name cannot be changed here.")
	doc = frappe.get_doc("Employee", emp)
	doc.employee_name = name
	doc.first_name = name
	doc.save(ignore_permissions=True)
	return {"employee": emp, "employee_name": doc.employee_name}
