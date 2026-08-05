"""Rename a staff member's login email, for a shop manager who is not an admin.

`frappe.client.rename_doc` cannot do this. Renaming a `User` is not one write: Frappe's
`User.after_rename` (frappe/core/doctype/user/user.py) renames the user's **Notification
Settings** document too, and that nested rename runs its own write-permission check. No
Barakat role holds write on `Notification Settings` — nobody outside System Manager does —
so the nested check throws

    You need write permission on Notification Settings <email> to rename

*after* the `User` row itself has been renamed, and the whole request rolls back. Measured
on a live bench: every persona below the shop owner failed, always, with the panel showing
only a generic "couldn't save". Correcting a typo in a staff login was therefore impossible
for the one role that is supposed to do it, and the only way through was to blank the email
and add it again — which builds a SECOND account and leaves the old one alive as a working
key into the shop (the exact hazard `renameLogin` in the proxy exists to close).

So the rename is performed here with permissions suspended, and the authorisation the
suspension removes is re-imposed above it, deliberately and narrowly:

  * the caller must hold `write` on `User` — that is the persona grant (Barakat Staff
    Writer / Staff Manager, or an owner), not something every login has;
  * the target must be a login this caller may already write, judged by the SAME row scope
    that gates reading and editing it (`user_scope.user_has_permission`) — i.e. staff of a
    company the caller's own Employee belongs to. A user from another shop, or one with no
    Employee at all (owner and service accounts), is refused;
  * a caller may never rename their OWN login: the rename force-clears that user's
    sessions, so it would kill the caller mid-request and drop the edit. The proxy refuses
    this too; it is repeated here because this method is reachable without the proxy.

Nothing here grants a permission that the caller did not already have on that document —
it only stops Frappe's own cascade from demanding a permission that no shop role can hold.

`merge` is never passed on: it would fold two staff members onto one account.
"""

import frappe
from frappe.model.rename_doc import rename_doc as _rename_doc
from frappe.utils import validate_email_address

from barakat.overrides.user_scope import user_has_permission


@frappe.whitelist(methods=["POST"])
def rename_login(old_email: str, new_email: str) -> dict:
	"""Rename the User `old_email` to `new_email`. Returns `{"user": <new name>}`."""
	old = (old_email or "").strip()
	new = (new_email or "").strip()

	if not old or not new:
		frappe.throw(frappe._("Both the current and the new login email are required."))

	if old == new:
		# Nothing to do. Not an error: a save that re-sends the same address is normal.
		return {"user": old, "renamed": False}

	caller = frappe.session.user
	if old == caller:
		frappe.throw(
			frappe._("You cannot change your own login email here."),
			frappe.PermissionError,
		)

	# Role grant first: the persona must be one that may write logins at all.
	if not frappe.has_permission("User", "write"):
		frappe.throw(
			frappe._("You are not allowed to change a staff member's login email."),
			frappe.PermissionError,
		)

	# Then the row scope — the same one that decides whether this caller may open or
	# edit that User in the first place. Without it, the role grant alone would reach
	# every login on the site, across shops.
	if not user_has_permission(old, "write", caller):
		frappe.throw(
			frappe._("You are not allowed to change a staff member's login email."),
			frappe.PermissionError,
		)

	if not frappe.db.exists("User", old):
		frappe.throw(
			frappe._("No login exists for {0}.").format(old), frappe.DoesNotExistError
		)

	# Checked before the rename so the collision reads as a collision. Frappe would
	# raise it too, but only from inside the rename, wrapped in its own message.
	if frappe.db.exists("User", new):
		frappe.throw(
			frappe._("User {0} already exists").format(new), frappe.DuplicateEntryError
		)

	validate_email_address(new, throw=True)

	# Run the rename as Administrator, and restore the caller no matter how it ends —
	# leaving the session elevated would hand every later write in this request the
	# same free pass.
	#
	# Passing `ignore_permissions` to the rename is NOT enough on its own: the flag
	# does not reach the nested `Notification Settings` rename that `User.after_rename`
	# performs, and that nested check is the one that fails (see the module docstring).
	# `frappe.flags.ignore_permissions` does not help either — Frappe 16's
	# `has_permission` has no such short-circuit; only `user == "Administrator"` does.
	caller_session = frappe.session.user
	try:
		frappe.set_user("Administrator")
		_rename_doc(doctype="User", old=old, new=new, ignore_permissions=True)
	finally:
		frappe.set_user(caller_session)

	return {"user": new, "renamed": True}
