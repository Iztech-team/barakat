"""Seed the per-shop rounding fields on every Company from the site-wide singles.

Rounding used to live in two site-wide singles (`Global Defaults.disable_rounded_total`,
`System Settings.rounding_method`) shared by every Company on the site — so changing
rounding for one shop changed it for all. The 2026-07-24 change moves both onto per-Company
custom fields (`custom_disable_rounded_total`, `custom_rounding_method`).

This one-time patch initializes those fields so the move is invisible: at migrate time no
shop has diverged yet, so every Company inherits the exact current site value. The write is
UNCONDITIONAL on purpose — a patch runs once by name, and this is that once. After it, the
AP writes each Company independently and shops can diverge.

Creates the two custom fields itself before seeding them. `bench migrate` runs
post_model_sync patches BEFORE `sync_fixtures`, so a patch cannot assume a field it
ships as a fixture exists yet — on the test bench this failed with
`Unknown column 'custom_disable_rounded_total' in 'SET'` and aborted the whole
migrate. `create_custom_fields` is idempotent, and the fixture sync later in the same
migrate simply re-applies the identical definition.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint

# Kept byte-identical to barakat/fixtures/custom_field.json — if that fixture changes,
# change this too, or a fresh site gets a field definition that the next migrate rewrites.
_FIELDS = {
	"Company": [
		{
			"fieldname": "custom_disable_rounded_total",
			"label": "Disable Rounded Total",
			"fieldtype": "Check",
			"default": "0",
			"insert_after": "round_off_account",
		},
		{
			"fieldname": "custom_rounding_method",
			"label": "Rounding Method",
			"fieldtype": "Select",
			"options": "Banker's Rounding (legacy)\nBanker's Rounding\nCommercial Rounding",
			"default": "Commercial Rounding",
			"insert_after": "custom_disable_rounded_total",
		},
	]
}

_METHOD_FALLBACK = "Commercial Rounding"
_ALLOWED_METHODS = (
	"Banker's Rounding (legacy)",
	"Banker's Rounding",
	"Commercial Rounding",
)


def execute():
	create_custom_fields(_FIELDS, ignore_validate=True)

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
