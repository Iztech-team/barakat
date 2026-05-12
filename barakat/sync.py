import frappe
import requests


def enqueue_user_sync(doc, method):
	"""Called on User after_insert — enqueues the sync to the short queue."""
	# Skip system users and the Administrator account
	if doc.name in ("Administrator", "Guest"):
		return

	frappe.enqueue(
		"barakat.sync.sync_user_to_master",
		queue="short",
		enqueue_after_commit=True,
		email=doc.email,
		first_name=doc.first_name,
		middle_name=doc.middle_name or None,
		last_name=doc.last_name or None,
		site_url=frappe.conf.get("site_url") or frappe.local.site,
	)


def sync_user_to_master(email, first_name, middle_name=None, last_name=None, site_url=None):
	"""Runs inside the sync worker — POSTs the new user to the master site."""
	master_url = frappe.conf.get("master_url")
	api_key = frappe.conf.get("master_api_key")
	api_secret = frappe.conf.get("master_api_secret")

	if not master_url:
		frappe.log_error(
			"master_url not set in site_config.json",
			"User Sync to Master"
		)
		return

	if not api_key or not api_secret:
		frappe.log_error(
			"master_api_key or master_api_secret not set in site_config.json",
			"User Sync to Master"
		)
		return

	payload = {
		"doctype": "User",
		"email": email,
		"first_name": first_name,
		"send_welcome_email": 1,
	}
	if middle_name:
		payload["middle_name"] = middle_name
	if last_name:
		payload["last_name"] = last_name

	try:
		response = requests.post(
			f"{master_url}/api/resource/User",
			headers={
				"Authorization": f"token {api_key}:{api_secret}",
				"Content-Type": "application/json",
			},
			json=payload,
			timeout=10,
		)
		response.raise_for_status()

	except Exception as e:
		frappe.log_error(
			f"Failed to create user {email} on master: {e}",
			"User Sync to Master"
		)
		raise  # re-raise so the queue retries the job

	# Only runs if user creation succeeded
	if site_url:
		try:
			mapping_response = requests.post(
				f"{master_url}/api/resource/User Site Mapping",
				headers={
					"Authorization": f"token {api_key}:{api_secret}",
					"Content-Type": "application/json",
				},
				json={
					"doctype": "User Site Mapping",
					"user": email,
					"site_url": site_url,
					"is_active": 1,
				},
				timeout=10,
			)
			mapping_response.raise_for_status()
		except Exception as e:
			frappe.log_error(
				f"User {email} created on master but failed to create User Site Mapping: {e}",
				"User Site Mapping Sync"
			)
			# No raise — user was created successfully, mapping failure is non-fatal
		)
		raise  # re-raise so the queue retries the job
