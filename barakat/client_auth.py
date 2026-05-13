import frappe
import requests


@frappe.whitelist(allow_guest=True)
def redeem_client_token(token: str):
    """
    Called by the POS desktop app on the CLIENT site.
    Verifies the token with the master site, then creates a local session
    for the user and returns the sid via Set-Cookie header.

    The POS app should capture the 'sid' value from the Set-Cookie response header.
    """
    master_url = frappe.conf.get("master_url")
    api_key = frappe.conf.get("master_api_key")
    api_secret = frappe.conf.get("master_api_secret")

    if not master_url or not api_key or not api_secret:
        frappe.throw(
            "This site is not configured to authenticate with a master site. "
            "Set master_url, master_api_key, and master_api_secret in site_config.json.",
            frappe.ValidationError,
        )

    site_url = frappe.conf.get("site_url") or frappe.local.site

    # Derive the master site hostname for the Host header (needed when master_url
    # is an internal IP like http://172.x.x.x:8000 but Frappe routes by hostname).
    from urllib.parse import urlparse as _urlparse
    master_host = frappe.conf.get("master_hostname") or _urlparse(master_url).hostname or "master.localhost"

    # Call master to verify the token
    try:
        resp = requests.post(
            f"{master_url}/api/method/barakat_master.api.verify_client_token",
            headers={
                "Authorization": f"token {api_key}:{api_secret}",
                "Content-Type": "application/json",
                "Host": master_host,
            },
            json={"token": token, "site_url": site_url},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else str(e)
        frappe.throw(
            f"Token verification failed: {body}",
            frappe.AuthenticationError,
        )
    except Exception as e:
        frappe.throw(
            f"Could not reach master site to verify token: {e}",
            frappe.AuthenticationError,
        )

    data = resp.json()
    message = data.get("message") or {}
    user_email = message.get("user") if isinstance(message, dict) else None

    if not user_email:
        frappe.throw(
            "Invalid response from master site — no user returned.",
            frappe.AuthenticationError,
        )

    # Ensure the user exists locally (use raw SQL to bypass any guest-context filters)
    user_exists = frappe.db.sql(
        "SELECT `name` FROM `tabUser` WHERE `name` = %s LIMIT 1", (user_email,)
    )
    if not user_exists:
        # Auto-create the user — master already verified the token so they are legitimate
        user_doc = frappe.new_doc("User")
        user_doc.email = user_email
        user_doc.first_name = user_email.split("@")[0]
        user_doc.send_welcome_email = 0
        user_doc.user_type = "System User"
        user_doc.insert(ignore_permissions=True)
        frappe.db.commit()

    # Create a local Frappe session for this user using the standard login flow
    login_manager = frappe.auth.LoginManager()
    login_manager.user = user_email
    login_manager.post_login()

    full_name = frappe.db.get_value("User", user_email, "full_name") or user_email

    # frappe.local.session.sid is set by post_login()
    sid = frappe.local.session.sid

    return {
        "email": user_email,
        "full_name": full_name,
        "sid": sid,
    }
