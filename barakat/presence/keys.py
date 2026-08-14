"""The credential each till reports with.

One key per till, never a shared secret: a shared key leaks once and every shop of every
client is exposed forever, with no fix short of redeploying the world.

The key's user holds **no DocPerm on anything**. It can call one whitelisted method and
that is the whole of its reach. So the method is the entire security boundary, and a
stolen key is worth close to nothing - it can post false device lists for one till at one
branch, and cannot read a single row.

It must never be the POS's own login. That account can read customers, prices and
invoices; a watcher key must not be a till account.
"""

import hashlib

import frappe
from frappe.utils import validate_email_address

WATCHER_ROLE = "Barakat Presence Watcher"


def ensure_watcher_role():
	"""A role with no permissions at all. It exists only to be assignable.

	Frappe needs a user to hold at least one role to be a System User in good standing,
	and this one grants nothing. `desk_access` is off: a watcher never opens a page.
	"""
	if frappe.db.exists("Role", WATCHER_ROLE):
		return WATCHER_ROLE

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": WATCHER_ROLE,
			"desk_access": 0,
			"is_custom": 1,
		}
	).insert(ignore_permissions=True)
	return WATCHER_ROLE


def user_name_for(till):
	"""Deterministic, so re-approving a till never orphans an account.

	The obvious address is the till's own name, and for a profile called "Front Counter"
	it is a perfectly legal one. It is not always legal. A POS Profile named in Arabic
	produces an Arabic address, Frappe refuses it as an email, and the till is answered
	417 by every request it will ever make — no key, no watcher, no attendance, and a row
	in the Admin Panel reading Active the whole time.

	Which is not an edge case for these shops, it is the ordinary case: on 2026-08-13 a
	till asked twice a minute for fifty-one minutes on that account, and is still Active
	with no key today.

	So the readable address is kept whenever it is valid, and a fingerprint of the very
	same name stands in when it is not. Keeping it matters as much as fixing it — moving
	every till to a new scheme would orphan the account each one is already authenticating
	with. The Arabic name is not lost: it stays on the user's `first_name`, which has no
	such restriction.
	"""
	readable = f"presence-till-{till.name}@barakat.local".replace(" ", "-").lower()
	if validate_email_address(readable):
		return readable

	# Truncated only as far as collision resistance allows; this names a login.
	digest = hashlib.sha256(str(till.name).encode("utf-8")).hexdigest()[:24]
	return f"presence-till-{digest}@barakat.local"


def account_for(till):
	"""The account this till actually holds, which outranks any rule for deriving one.

	`user_name_for` has to be able to change — it just did — and every derivation is a
	guess about the past. The till records what it was really given, so a reissue rotates
	the key the watcher is using and a revoke disables the account that is really open.
	Deriving instead would have quietly done neither, while reporting success.
	"""
	return till.get("api_user") or user_name_for(till)


def issue_key(till):
	"""Create (or reuse) the till's user and return a freshly generated key pair.

	Returned exactly once, at approval. There is no way to read it back afterwards -
	Frappe stores only a hash of the secret - so a lost key is reissued, never
	recovered. That is deliberate: nothing on the server can leak a live credential.
	"""
	from frappe.core.doctype.user.user import generate_keys

	ensure_watcher_role()
	email = account_for(till)

	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": f"Presence Till {till.name}",
				"user_type": "System User",
				"send_welcome_email": 0,
				"enabled": 1,
			}
		)
		user.insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)
		user.enabled = 1

	_assign_role(user)

	# `generate_keys` rotates api_secret and returns the plaintext once.
	secret = generate_keys(user.name).get("api_secret")
	api_key = frappe.db.get_value("User", user.name, "api_key")

	return {"user": user.name, "api_key": api_key, "api_secret": secret}


def _assign_role(user):
	"""Give the watcher its (empty) role, working around the permlevel-1 trap.

	`User.roles` is permlevel 1, writable only by System Manager. `add_roles()` ends in
	a plain `save()`, so when a **Manager** approves a till Frappe silently drops the
	role rows - no error, HTTP 200, and a watcher that can never authenticate. Writing
	the child table and saving with `ignore_permissions` is the documented way round it.

	This has cost this codebase real time before. Do not simplify it back to
	`user.add_roles(...)`.
	"""
	held = {row.role for row in user.roles}
	if WATCHER_ROLE not in held:
		user.append("roles", {"role": WATCHER_ROLE})
	user.save(ignore_permissions=True)

	assert frappe.db.exists(
		"Has Role", {"parent": user.name, "role": WATCHER_ROLE}
	), "watcher role was dropped on save - the permlevel-1 trap has bitten again"


def revoke(till):
	"""Disable the till's account. History is kept; the key stops working at once."""
	email = account_for(till)
	if frappe.db.exists("User", email):
		frappe.db.set_value("User", email, "enabled", 0)


def till_for_current_user():
	"""The `Presence Till` this request is authenticated as, or None.

	This is where scope comes from. The request body never names a company, a branch or
	a till - the key already answered all three, so a caller cannot claim to be
	somewhere else. Same rule that failed once on Contact and Item Price: one
	enforcement point, never a filter the caller supplies.
	"""
	name = frappe.db.get_value("Presence Till", {"api_user": frappe.session.user})
	if not name:
		return None
	return frappe.get_doc("Presence Till", name)
