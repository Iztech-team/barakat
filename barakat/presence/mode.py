"""Is wifi presence switched on for this company, and with what numbers.

Every presence entry point starts here. A company that has never been switched on has
no settings row at all, and must behave exactly as it did before this feature existed:
no endpoint, no jobs, no screens. `petromall` is that case permanently, and it is why
the default lives in code rather than in a row someone has to remember to create.

Every threshold is here rather than in code because changing a number on the bench
otherwise needs a pull and a restart by hand on production, while changing a setting
does not. Anything we expect to fiddle with belongs in this table.
"""

import frappe

MANUAL = "Manual"
WIFI = "Wifi"

DEFAULTS = {
	"mode": MANUAL,
	"departure_wait_minutes": 15,
	"sweep_interval_s": 2,
	"heartbeat_s": 30,
	"warmup_s": 60,
	"sighting_retention_days": 30,
	"pairing_timeout_s": 120,
	"rot_days": 7,
	"max_devices": 512,
}


def settings_for(company):
	"""This company's presence settings, falling back to `DEFAULTS` field by field.

	Field by field on purpose: a row that sets only the departure wait must not zero
	out every other number just by existing.

	A cleared Int field in Frappe reads back as **0**, not NULL - the column is NOT
	NULL DEFAULT 0. So zero has to mean "not set" here, and every one of these numbers
	is a duration where zero is never a sane answer: a zero departure wait would send
	an entire shop home on the first missed sweep, and a zero warm-up would do it every
	time a till rebooted. If a real zero is ever wanted for one of these, it needs its
	own explicit flag rather than a magic value.
	"""

	values = dict(DEFAULTS)
	if not company:
		return values

	row = frappe.db.get_value(
		"Presence Settings",
		{"custom_company": company},
		list(DEFAULTS),
		as_dict=True,
	)
	if not row:
		return values

	for key, fallback in DEFAULTS.items():
		saved = row.get(key)
		if isinstance(fallback, int):
			if isinstance(saved, int) and saved > 0:
				values[key] = saved
		elif saved:
			values[key] = saved
	return values


def is_wifi_mode(company):
	"""True only when this company has deliberately turned wifi presence on."""

	return settings_for(company)["mode"] == WIFI
