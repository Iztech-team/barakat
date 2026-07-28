"""Pure input validation for the root-account renamer.

Split out of `chart_of_accounts.py` so it can be unit tested without a Frappe
site — same reason `patches/_uom_scope_logic.py` exists. The leading underscore
keeps it out of the whitelisted-API namespace: nothing here is callable over
HTTP.
"""

import json

# An Account.account_name is a Data field; Frappe's limit is 140. Keep inside
# it and refuse anything that looks like a mistake rather than a name.
MAX_ACCOUNT_NAME = 140


def clean_names(raw):
	"""Parse and validate a `{old_name: new_name}` mapping.

	Raises ValueError; the whitelisted caller turns that into a `frappe.throw`.
	"""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except (TypeError, ValueError):
			raise ValueError("names must be a JSON object of {old: new}")

	if not isinstance(raw, dict) or not raw:
		raise ValueError("names must be a non-empty JSON object of {old: new}")

	cleaned = {}
	for old, new in raw.items():
		if not isinstance(old, str) or not isinstance(new, str):
			raise ValueError("every key and value must be a string")
		old_s, new_s = old.strip(), new.strip()
		if not old_s or not new_s:
			raise ValueError("names cannot be blank")
		if len(new_s) > MAX_ACCOUNT_NAME:
			raise ValueError(f"name too long: {new_s[:30]}...")
		# A newline or a tab in an account name is always a mistake, and would
		# render as a broken row everywhere the chart is displayed.
		if any(ch in new_s for ch in "\r\n\t"):
			raise ValueError("names cannot contain line breaks or tabs")
		cleaned[old_s] = new_s

	if len(set(cleaned.values())) != len(cleaned):
		raise ValueError("two roots would end up with the same name")

	return cleaned
