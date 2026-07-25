"""Seed the per-shop rounding fields on every Company from the site-wide singles.

Rounding used to live in two site-wide singles (`Global Defaults.disable_rounded_total`,
`System Settings.rounding_method`) shared by every Company on the site — so changing
rounding for one shop changed it for all. The 2026-07-24 change moves both onto per-Company
custom fields (`custom_disable_rounded_total`, `custom_rounding_method`).

This one-time patch initializes those fields so the move is invisible: at migrate time no
shop has diverged yet, so every Company inherits the exact current site value. The write is
UNCONDITIONAL on purpose — a patch runs once by name, and this is that once. After it, the
AP writes each Company independently and shops can diverge.

Runs post_model_sync, so the custom fields (synced from fixtures earlier in the same
migrate) already exist.
"""

import frappe
from frappe.utils import cint

_METHOD_FALLBACK = "Commercial Rounding"
_ALLOWED_METHODS = (
	"Banker's Rounding (legacy)",
	"Banker's Rounding",
	"Commercial Rounding",
)


def execute():
	disable_raw = frappe.db.get_single_value("Global Defaults", "disable_rounded_total")
	disable = 1 if cint(disable_raw) else 0

	method = frappe.db.get_single_value("System Settings", "rounding_method")
	if method not in _ALLOWED_METHODS:
		method = _METHOD_FALLBACK

	for name in frappe.get_all("Company", pluck="name"):
		frappe.db.set_value(
			"Company",
			name,
			{
				"custom_disable_rounded_total": disable,
				"custom_rounding_method": method,
			},
			update_modified=False,
		)

	frappe.db.commit()
