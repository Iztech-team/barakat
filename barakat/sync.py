import frappe
import requests


def enqueue_user_sync(doc, method):
	"""Called on User after_insert — enqueues the sync to the dedicated sync queue."""
	# Skip system users and the Administrator account
	if doc.name in ("Administrator", "Guest"):
		return

	frappe.enqueue(
		"barakat.sync.sync_user_to_master",
		queue="sync",
		enqueue_after_commit=True,
		email=doc.email,
		first_name=doc.first_name,
		last_name=doc.last_name or "",
	)


def sync_user_to_master(email, first_name, last_name):
	"""Runs inside the sync worker — POSTs the new user to the master site."""
	master_url = frappe.conf.get("master_url", "http://master.localhost:8000")
	api_key = frappe.conf.get("master_api_key")
	api_secret = frappe.conf.get("master_api_secret")

	if not api_key or not api_secret:
		frappe.log_error(
			"master_api_key or master_api_secret not set in site_config.json",
			"User Sync to Master"
		)
		return

	try:
		response = requests.post(
			f"{master_url}/api/resource/User",
			headers={
				"Authorization": f"token {api_key}:{api_secret}",
				"Content-Type": "application/json",
			},
			json={
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
				"send_welcome_email": 1,
			},
			timeout=10,
		)
		response.raise_for_status()
	except Exception as e:
		frappe.log_error(
			f"Failed to sync user {email} to master: {e}",
			"User Sync to Master"
		)
		raise  # re-raise so the queue retries the job
