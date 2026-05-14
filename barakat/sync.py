import json

import frappe
import requests

# Fields synced between sites. Excludes: computed fields (full_name), site-specific
# permissions (roles, user_type, module_profile), credentials (api_key, api_secret,
# passwords handled separately), session data, and local file attachments (user_image).
SYNC_FIELDS = [
	"first_name", "middle_name", "last_name", "username",
	"phone", "mobile_no", "birth_date", "location", "gender",
	"bio", "interest", "language", "time_zone", "enabled",
	"desk_theme", "search_bar", "notifications", "list_sidebar",
	"bulk_actions", "view_switcher", "form_sidebar", "form_navigation_buttons",
	"timeline", "dashboard", "show_absolute_datetime_in_timeline",
	"code_editor_type", "mute_sounds", "default_workspace", "default_app",
	"thread_notify", "send_me_a_copy", "allowed_in_mentions",
	"document_follow_notify", "document_follow_frequency",
	"follow_created_documents", "follow_commented_documents",
	"follow_liked_documents", "follow_shared_documents",
	"follow_assigned_documents",
]

_SKIP_USERS = {"Administrator", "Guest"}


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def capture_new_password(doc, method):
	"""before_validate hook — stash plaintext password before Frappe clears it."""
	if doc.new_password:
		doc.flags.sync_new_password = doc.new_password


def enqueue_user_sync(doc, method):
	"""on_update hook — enqueue full user sync to master."""
	if doc.name in _SKIP_USERS:
		return
	if frappe.flags.get("from_master_sync"):
		return  # this save was triggered by a master push — don't loop back

	payload = {field: doc.get(field) for field in SYNC_FIELDS}
	payload["email"] = doc.name

	new_password = doc.flags.get("sync_new_password")
	if new_password:
		payload["new_password"] = new_password

	site_url = frappe.conf.get("site_url") or frappe.local.site

	frappe.enqueue(
		"barakat.sync.sync_user_to_master",
		queue="short",
		enqueue_after_commit=True,
		payload=payload,
		site_url=site_url,
	)


# ---------------------------------------------------------------------------
# Background job: client → master
# ---------------------------------------------------------------------------

def sync_user_to_master(payload, site_url):
	"""Background worker job — upsert user on master and ensure site mapping exists."""
	master_url = frappe.conf.get("master_url")
	api_key = frappe.conf.get("master_api_key")
	api_secret = frappe.conf.get("master_api_secret")

	if not master_url or not api_key or not api_secret:
		frappe.log_error(
			"master_url / master_api_key / master_api_secret not set in site_config.json",
			"User Sync to Master",
		)
		return

	email = payload["email"]
	headers = {
		"Authorization": f"token {api_key}:{api_secret}",
		"Content-Type": "application/json",
		"X-Barakat-Sync": "1",
	}

	# Upsert: try PUT first, fall back to POST on 404
	# Strip email from PUT body — it's already in the URL
	# send_welcome_email is always 0 — master must never send its own welcome email
	put_payload = {k: v for k, v in payload.items() if k != "email"}
	put_payload["send_welcome_email"] = 0
	try:
		resp = requests.put(
			f"{master_url}/api/resource/User/{email}",
			headers=headers,
			json=put_payload,
			timeout=10,
		)
		if resp.status_code == 404:
			create_payload = {k: v for k, v in payload.items()}
			create_payload["doctype"] = "User"
			create_payload["user_type"] = "Website User"
			create_payload["send_welcome_email"] = 0
			resp = requests.post(
				f"{master_url}/api/resource/User",
				headers=headers,
				json=create_payload,
				timeout=10,
			)
		resp.raise_for_status()
	except Exception as e:
		frappe.log_error(f"Failed to sync user {email} to master: {e}", "User Sync to Master")
		raise  # re-raise so the queue retries

	# Ensure User Site Mapping exists
	if not site_url:
		return
	try:
		check = requests.get(
			f"{master_url}/api/resource/User Site Mapping",
			headers=headers,
			params={
				"filters": json.dumps([["user", "=", email], ["site_url", "=", site_url]]),
				"limit": 1,
				"fields": json.dumps(["name"]),
			},
			timeout=10,
		)
		check.raise_for_status()
		existing = check.json().get("data", [])

		if existing:
			requests.put(
				f"{master_url}/api/resource/User Site Mapping/{existing[0]['name']}",
				headers=headers,
				json={"is_active": 1},
				timeout=10,
			).raise_for_status()
		else:
			requests.post(
				f"{master_url}/api/resource/User Site Mapping",
				headers=headers,
				json={
					"doctype": "User Site Mapping",
					"user": email,
					"site_url": site_url,
					"is_active": 1,
				},
				timeout=10,
			).raise_for_status()
	except Exception as e:
		frappe.log_error(
			f"User {email} synced to master but failed to upsert User Site Mapping: {e}",
			"User Site Mapping Sync",
		)
		# Non-fatal — user was synced successfully


# ---------------------------------------------------------------------------
# Endpoint: master → client
# ---------------------------------------------------------------------------

@frappe.whitelist()
def receive_user_from_master(user_data, new_password=None):
	"""Called by barakat_master to push user updates to this client site."""
	if isinstance(user_data, str):
		user_data = json.loads(user_data)

	email = user_data.get("email")
	if not email or email in _SKIP_USERS:
		return

	frappe.flags.from_master_sync = True
	try:
		if frappe.db.exists("User", email):
			user = frappe.get_doc("User", email)
			for field in SYNC_FIELDS:
				if field in user_data:
					user.set(field, user_data[field])
			if new_password:
				user.new_password = new_password
			user.save(ignore_permissions=True)
		else:
			user = frappe.new_doc("User")
			user.email = email
			user.user_type = "Website User"
			user.send_welcome_email = 0
			for field in SYNC_FIELDS:
				if field in user_data:
					user.set(field, user_data[field])
			if new_password:
				user.new_password = new_password
			user.insert(ignore_permissions=True)

		frappe.db.commit()
	finally:
		frappe.flags.from_master_sync = False


