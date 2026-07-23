import frappe
from frappe import _

# Roles allowed to bind a till to a POS Profile. Every method here writes through
# frappe.db.set_value / insert(ignore_permissions=True), which bypass ERPNext's
# permission engine entirely — so this constant is the only access control on the
# device-to-profile mapping. `Barakat POS Operator` is held by the Branch Supervisor
# and Manager personas ONLY — the Cashier lost it in 07669ff when that persona was
# made read-only, so a Cashier can no longer bind a device. Do not re-add it to the
# Cashier bundle to "fix" a binding failure; bind under a supervisor/manager session.
POS_DEVICE_ROLES = ("Barakat POS Operator", "System Manager")


@frappe.whitelist()
def register_device(device_id: str, device_name: str) -> dict:
	"""
	Called on first app launch. Creates the Device record if it doesn't exist.
	Returns the device record so the app knows if it already existed.
	"""
	frappe.only_for(POS_DEVICE_ROLES)
	if not device_id or not device_name:
		frappe.throw(_("device_id and device_name are required."))

	existed = frappe.db.exists("Device", device_id)
	if not existed:
		frappe.get_doc({
			"doctype": "Device",
			"device_id": device_id,
			"device_name": device_name,
		}).insert(ignore_permissions=True)
		frappe.db.commit()

	device = frappe.db.get_value(
		"Device", device_id, ["device_id", "device_name"], as_dict=True
	)
	return {"device": device, "is_new": not existed}


@frappe.whitelist()
def get_available_profiles(branch: str, device_id: str) -> list:
	"""
	Returns POS Profiles for this branch that are either:
	  - Not linked to any device (available)
	  - Already linked to THIS device (re-selectable)

	Also returns the profile currently linked to this device on this branch (if any).
	"""
	frappe.only_for(POS_DEVICE_ROLES)
	if not branch or not device_id:
		frappe.throw(_("branch and device_id are required."))

	# Get all profiles assigned to this branch
	rows = frappe.db.get_all(
		"Branch POS Profile",
		filters={"parent": branch, "parenttype": "Branch"},
		pluck="pos_profile",
	)
	if not rows:
		return []

	profiles = frappe.db.get_all(
		"POS Profile",
		filters={"name": ("in", rows)},
		fields=["name", "custom_device"],
	)

	result = []
	for p in profiles:
		linked_device = p.get("custom_device") or None
		is_mine = linked_device == device_id
		is_available = linked_device is None

		if is_available or is_mine:
			result.append({
				"pos_profile": p["name"],
				"is_mine": is_mine,
			})

	return result


@frappe.whitelist()
def select_profile(device_id: str, pos_profile: str) -> dict:
	"""
	Links this device to the chosen POS Profile immediately.
	Clears any previous profile this device was linked to.
	"""
	frappe.only_for(POS_DEVICE_ROLES)
	if not device_id or not pos_profile:
		frappe.throw(_("device_id and pos_profile are required."))

	# Verify the profile is available (unlinked or already mine)
	current_device = frappe.db.get_value("POS Profile", pos_profile, "custom_device")
	if current_device and current_device != device_id:
		frappe.throw(
			_("POS Profile {0} is already taken by another device.").format(pos_profile),
			frappe.PermissionError,
		)

	# Clear previous profile this device owned
	old_profile = frappe.db.get_value(
		"POS Profile", {"custom_device": device_id}, "name"
	)
	if old_profile and old_profile != pos_profile:
		frappe.db.set_value("POS Profile", old_profile, "custom_device", None)

	# Link this device to the new profile
	frappe.db.set_value("POS Profile", pos_profile, "custom_device", device_id)
	frappe.db.commit()

	return {"ok": True, "pos_profile": pos_profile}


@frappe.whitelist()
def check_device_profile(device_id: str, pos_profile: str) -> dict:
	"""
	Called on every branch selection to verify the device-profile mapping is still valid.
	Returns status: "ok" | "changed" | "unlinked"
	"""
	frappe.only_for(POS_DEVICE_ROLES)
	if not device_id or not pos_profile:
		frappe.throw(_("device_id and pos_profile are required."))

	current_device = frappe.db.get_value("POS Profile", pos_profile, "custom_device")

	if current_device == device_id:
		return {"status": "ok", "pos_profile": pos_profile}

	if current_device:
		# Profile was reassigned to a different device by admin
		return {"status": "changed", "pos_profile": pos_profile, "now_linked_to": current_device}

	# Profile was unlinked by admin
	return {"status": "unlinked", "pos_profile": pos_profile}
