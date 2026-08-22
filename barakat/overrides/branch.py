import frappe
from frappe import _

from barakat.barakat.doctype.presence_till.presence_till import resolve_scope


def validate_branch(doc, method):
	# custom_pos_profiles only exists after after_install creates the custom field
	if not hasattr(doc, "custom_pos_profiles"):
		return
	_validate_unique_pos_profiles(doc)
	_validate_profiles_not_in_other_branches(doc)
	_sync_branch_back_reference(doc)


def _validate_unique_pos_profiles(doc):
	profiles = [row.pos_profile for row in (doc.custom_pos_profiles or []) if row.pos_profile]
	if len(profiles) != len(set(profiles)):
		frappe.throw(_("Each POS Profile can only appear once in a branch's profile list."))


def _validate_profiles_not_in_other_branches(doc):
	for row in (doc.custom_pos_profiles or []):
		if not row.pos_profile:
			continue
		other = frappe.db.sql(
			"""
			SELECT parent FROM `tabBranch POS Profile`
			WHERE pos_profile = %s AND parent != %s
			""",
			(row.pos_profile, doc.name or "__new__"),
			as_dict=True,
		)
		if other:
			frappe.throw(
				_("POS Profile {0} is already assigned to branch {1}. A profile can only belong to one branch.").format(
					row.pos_profile, other[0].parent
				)
			)


def _sync_branch_back_reference(doc):
	"""Write custom_branch on each POS Profile that belongs to this branch."""
	profiles_in_doc = {row.pos_profile for row in (getattr(doc, "custom_pos_profiles", None) or []) if row.pos_profile}

	# Set custom_branch on profiles now in this branch
	for profile in profiles_in_doc:
		frappe.db.set_value("POS Profile", profile, "custom_branch", doc.name)

	# Clear custom_branch on profiles that were removed from this branch
	previously_linked = frappe.db.get_all(
		"POS Profile",
		filters={"custom_branch": doc.name},
		pluck="name",
	)
	for profile in previously_linked:
		if profile not in profiles_in_doc:
			frappe.db.set_value("POS Profile", profile, "custom_branch", None)


def branch_on_update(doc, method=None):
	"""Move a till's attendance when its profile is moved to another branch.

	🚨 A `Presence Till` stores its branch and its company, and its `validate` refreshes
	them from the `Branch POS Profile` table - but only when that document is SAVED, and
	nothing saves it in normal operation: a report writes `last_seen` through
	`frappe.db.set_value(update_modified=False)`, which bypasses validation entirely.

	So reassigning a POS Profile to a different branch used to change nothing at all for
	presence. Every sighting kept landing on the branch the till had left, silently and
	for ever - one person marked present in a shop they are not in, and the branch they
	ARE in showing empty. There is no error to notice; the numbers are simply wrong.

	The two fields are written directly rather than by re-saving the till, and the
	answer comes from `resolve_scope` - the same function the till's own `validate`
	uses, so what a till's branch is stays decided in one place. Re-saving would drag
	the whole document's validation into a manager's branch edit, and any unrelated
	failure in it would block that edit.

	A profile that now belongs to no branch has no answer at all. That is logged, with
	the reason, and the branch edit still goes through: the till stops being able to
	report either way, which is a state its own screen already describes, and a branch
	nobody can edit is worse.
	"""
	if not hasattr(doc, "custom_pos_profiles"):
		return

	profiles = {row.pos_profile for row in (doc.custom_pos_profiles or []) if row.pos_profile}
	# Both directions of a move: the profiles this branch now claims, and any till still
	# pointing here from a profile that has just been taken away.
	stale = set(
		frappe.db.get_all("Presence Till", filters={"branch": doc.name}, pluck="pos_profile")
	)

	for profile in profiles | stale:
		till = frappe.db.get_value("Presence Till", {"pos_profile": profile}, "name")
		if not till:
			continue
		try:
			branch, company = resolve_scope(profile)
		except Exception:
			# WITH the reason. A log line saying only that something failed is the same
			# silence this hook exists to remove, one level up.
			frappe.log_error(
				title="Presence till branch resync failed",
				message=(
					f"POS Profile {profile} on branch {doc.name}\n\n"
					f"{frappe.get_traceback()}"
				),
			)
			continue
		frappe.db.set_value(
			"Presence Till", till, {"branch": branch, "custom_company": company}
		)
