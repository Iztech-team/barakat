app_name = "barakat"
app_title = "Barakat"
app_publisher = "Osama Tbaileh"
app_description = "Barakat custom ERP configuration and extensions"
app_email = "osamatbaileh@iztechvalley.ps"
app_license = "mit"

from barakat.permissions import EXPORTED_ROLE_NAMES
from barakat.persona_matrix import MODULE_DOCTYPES

# Every doctype any persona can reach. Imported from `persona_matrix` (which is
# deliberately Frappe-free) rather than from `overrides.company_scope`, so hooks.py
# keeps loading without pulling frappe in through the side door.
COMPANY_SCOPED_DOCTYPES = tuple(
	sorted({dt for doctypes in MODULE_DOCTYPES.values() for dt in doctypes})
)

fixtures = [
	{
		"dt": "DocType",
		"filters": [["name", "in", ["POS Employee Branch", "Device", "Branch POS Profile"]]],
	},
	{
		"dt": "Role",
		# Computed, never hand-listed. There are now ~55 Barakat roles (40 generated
		# from the module matrix), and a role this app fails to export is SILENTLY
		# dropped: persona_role_bundle() intersects a bundle with the roles that exist
		# on the site, so the user ends up with fewer roles and no error anywhere.
		# Guarded by RoleFixtureCoverage in overrides/test_persona_guard.py.
		"filters": [["name", "in", EXPORTED_ROLE_NAMES]],
	},
	{
		"dt": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				[
					# Company (per-shop rounding — see barakat.api.settings notes)
					"Company-custom_disable_rounded_total",
					"Company-custom_rounding_method",
					"Company-custom_barakat_coa_language",
					# Pre-existing fields that were never added here. They install
					# fine (import reads the whole file) but export-fixtures would
					# drop them. Found by EveryFixtureFieldIsExported, 2026-08-11.
					"Company-custom_barakat_opening_account",
					"Company-custom_barakat_salary_expense_account",
					"Company-custom_scale_uom",
					# Branch
					"Branch-custom_pos_company",
					"Branch-custom_pos_profiles",
					# POS Profile
					"POS Profile-custom_barakat_section",
					"POS Profile-custom_device",
					"POS Profile-custom_branch",
					"POS Profile-custom_cash_account",
					"POS Profile-custom_salary_advance_account",
					"POS Profile-custom_expense_account",
					"POS Profile-custom_owner_deposit_account",
					"POS Profile-custom_bank_account",
					# POS Profile — per-till cashier limits. Absent from this list,
					# `bench export-fixtures` would quietly drop them from
					# custom_field.json and the next site to install would have no
					# discount cap at all.
					"POS Profile-custom_cashier_limits_section",
					"POS Profile-custom_allow_ad_hoc_item",
					"POS Profile-custom_allow_customer_creation",
					"POS Profile-custom_max_discount_percent",
					# Employee
					"Employee-custom_pos_pin",
					"Employee-custom_role_preset",
					"Employee-custom_pos_branches",
					"Employee-custom_base_salary",
					"Employee-custom_salary_currency",
					"Employee-custom_salary_period",
					"Employee-custom_expected_work_days",
					"Employee-custom_work_start_time",
					"Employee-custom_work_end_time",
					# Item / Item Group
					"Item-custom_company",
					"Item Group-custom_company",
					# Supplier
					"Supplier-custom_company",
					# Mode of Payment
					"Mode of Payment-custom_company",
					# Currency Exchange
					"Currency Exchange-custom_company",
					# Customer
					"Customer-custom_company",
					"Customer-custom_branch",
					"Customer-custom_date_of_birth",
					# Set by the till so Customer.before_insert can tell a till's
					# request from the admin panel's.
					"Customer-custom_pos_profile",
					# Company markers on doctypes ERPNext gives no company field.
					# Without these the Company User Permission has no Link field to
					# bind to and the rows are readable by every shop on the site —
					# see barakat.overrides.company_marker.
					"Contact-custom_company",
					"Item Price-custom_company",
					"Product Bundle-custom_company",
					"Supplier Group-custom_company",
					"Territory-custom_company",
					# Same omission as the Company fields above — these three are
					# tenant-scoping markers, so an export that dropped them would
					# make those rows readable by every shop on the site.
					"Customer Group-custom_company",
					"Price List-custom_company",
					"UOM-custom_company",
					# Sales Invoice / POS Invoice
					"Sales Invoice-custom_external_id",
					"Sales Invoice-custom_operator_employee",
					"POS Invoice-custom_external_id",
					"POS Invoice-custom_operator_employee",
					# Journal Entry
					"Journal Entry-custom_external_id",
					"Journal Entry-custom_pos_opening_entry",
					# POS Opening / Closing Entry
					"POS Opening Entry-custom_device_id",
					"POS Opening Entry-custom_opened_by_staff",
					"POS Closing Entry-custom_device_id",
					"POS Closing Entry-custom_closed_by_staff",
					"POS Closing Entry-custom_closed_by_user",
					# Attendance
					"Attendance-custom_branch",
				],
			]
		],
	},
]

after_install = "barakat.setup.install.after_install"
setup_wizard_complete = "barakat.setup.install.after_setup_wizard"
after_migrate = "barakat.setup.install.after_migrate"

doc_events = {
	"Employee": {
		# Runs BEFORE the controller's own validate(), which is the only place a
		# per-company duplicate-login rule can replace ERPNext's site-wide one.
		"before_validate": "barakat.overrides.employee_identity.scope_duplicate_login_to_company",
		"validate": [
			"barakat.validations.validate_employee_pin",
			"barakat.overrides.staff_roles.guard_role_preset",
			"barakat.overrides.staff_roles.guard_user_permission_flag",
		],
		"after_insert": [
			"barakat.overrides.staff_roles.reassert_persona_roles",
			"barakat.overrides.staff_roles.reassert_company_user_permission",
		],
		"on_update": [
			"barakat.overrides.staff_roles.reassert_persona_roles",
			"barakat.overrides.staff_roles.reassert_company_user_permission",
		],
	},
	"Attendance": {
		"validate": "barakat.validations.validate_attendance_branch",
	},
	"Branch": {
		"validate": "barakat.overrides.branch.validate_branch",
	},
	"Company": {
		"on_update": "barakat.scale_unit.company_on_update",
	},
	"POS Profile": {
		"validate": [
			"barakat.validations.validate_pos_profile_accounts",
			# A till's stock figures describe ONE warehouse and it only notices a
			# repoint on its next sync — so mid-shift it sells against the old
			# branch's numbers. See validations.py for the full reasoning.
			"barakat.validations.validate_pos_profile_warehouse_change",
		],
	},
	"Item": {
		"validate": "barakat.validations.validate_item_disable",
	},
	"Customer": {
		"validate": "barakat.validations.validate_customer_mobile_unique",
		# `before_insert`, not `validate`: the rule is about who may CREATE a
		# customer. Running it on every save would block edits to a customer a
		# till legitimately created before the flag was turned off.
		"before_insert": "barakat.overrides.customer_pos_guard.guard_pos_customer_creation",
	},
	# Inherit the owning company onto the three doctypes ERPNext gives no company
	# field. The marker is what the Company User Permission binds to; an unstamped
	# row is visible to every shop on the site. See barakat.overrides.company_marker.
	"Contact": {
		"validate": "barakat.overrides.company_marker.stamp_contact",
	},
	"Item Price": {
		"validate": "barakat.overrides.company_marker.stamp_item_price",
	},
	"Product Bundle": {
		"validate": "barakat.overrides.company_marker.stamp_product_bundle",
	},
	# `before_insert`, not `validate`: these have no parent to inherit from, so the
	# only source is the caller, and re-deriving on a later save would let one shop
	# claim a group every shop is already using. Existing rows stay shared.
	"Supplier Group": {
		"before_insert": "barakat.overrides.company_marker.stamp_new_owned_master",
	},
	"Territory": {
		"before_insert": "barakat.overrides.company_marker.stamp_new_owned_master",
	},
	"Loyalty Program": {
		"validate": "barakat.validations.validate_loyalty_program_tier_names",
	},
	"Pricing Rule": {
		"validate": "barakat.validations.validate_pricing_rule_disable",
		"on_trash": "barakat.validations.guard_pricing_rule_delete",
	},
}

doctype_js = {
	"Employee": "public/js/employee.js",
	"POS Profile": "public/js/pos_profile.js",
	"Item": "public/js/item.js",
}

override_doctype_class = {
	# Builds a new company's chart of accounts in the owner's language, so the
	# account IDS are Arabic/Hebrew too — they can never be renamed afterwards.
	"Company": "barakat.overrides.company.BarakatCompany",
	"POS Opening Entry": "barakat.overrides.pos_opening_entry.BarakatPOSOpeningEntry",
	"POS Closing Entry": "barakat.overrides.pos_closing_entry.BarakatPOSClosingEntry",
	"POS Invoice": "barakat.overrides.pos_invoice.BarakatPOSInvoice",
	"Sales Invoice": "barakat.overrides.sales_invoice.BarakatSalesInvoice",
}

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "barakat",
# 		"logo": "/assets/barakat/logo.png",
# 		"title": "Barakat",
# 		"route": "/barakat",
# 		"has_permission": "barakat.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/barakat/css/barakat.css"
# app_include_js = "/assets/barakat/js/barakat.js"

# include js, css files in header of web template
# web_include_css = "/assets/barakat/css/barakat.css"
# web_include_js = "/assets/barakat/js/barakat.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "barakat/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "barakat/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "barakat.utils.jinja_methods",
# 	"filters": "barakat.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "barakat.install.before_install"
# after_install = "barakat.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "barakat.uninstall.before_uninstall"
# after_uninstall = "barakat.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "barakat.utils.before_app_install"
# after_app_install = "barakat.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "barakat.utils.before_app_uninstall"
# after_app_uninstall = "barakat.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "barakat.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "barakat.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# `Barakat Supplier Ledger Reader` holds a plain GL Entry read DocPerm, which is
# per-doctype and would therefore expose the whole ledger. These two hooks narrow
# its holders to supplier rows; everyone else is returned untouched. See
# barakat/overrides/gl_entry.py and barakat.permissions.gl_entry_scope_for.
permission_query_conditions = {
	"GL Entry": "barakat.overrides.gl_entry.get_permission_query_conditions",
	# Scope `Barakat Self Service` holders to their own rows. Both this and the
	# has_permission entry below are required — the query condition covers lists, the
	# controller hook covers opening one document directly.
	"Employee": "barakat.overrides.self_service.employee_query_conditions",
	"Salary Slip": "barakat.overrides.self_service.salary_slip_query_conditions",
	# `User` read comes from Frappe's desk baseline, not from any Barakat role, so no
	# persona rule ever covered it — a Cashier listed every colleague, and a Manager of
	# one shop listed another shop's staff on a multi-company site. See
	# barakat.overrides.user_scope.
	"User": "barakat.overrides.user_scope.user_query_conditions",
}

has_permission = {
	"GL Entry": "barakat.overrides.gl_entry.has_permission",
	"Employee": "barakat.overrides.self_service.employee_has_permission",
	"Salary Slip": "barakat.overrides.self_service.salary_slip_has_permission",
	"User": "barakat.overrides.user_scope.user_has_permission",
}


def _add_company_scope(existing, method):
	"""Register the multi-company guard on every doctype a persona can reach.

	GENERATED, never hand-listed, from the same `MODULE_DOCTYPES` map the persona role
	bundles are built from — so a doctype a persona can touch is guarded by
	construction. Hand-picking the doctypes is what makes this class of guard look
	like coverage while leaving holes.

	Frappe accepts a list for either hook and runs every entry, so this APPENDS to the
	four doctypes that already carry a narrower rule rather than replacing them. Order
	does not matter: both hooks can only deny, so the result is the intersection
	whichever runs first.
	"""
	merged = dict(existing)
	for doctype in COMPANY_SCOPED_DOCTYPES:
		current = merged.get(doctype)
		if current is None:
			merged[doctype] = method
		elif isinstance(current, str):
			merged[doctype] = [current, method]
		else:
			merged[doctype] = [*current, method]
	return merged


permission_query_conditions = _add_company_scope(
	permission_query_conditions,
	"barakat.overrides.company_scope.get_permission_query_conditions",
)
has_permission = _add_company_scope(
	has_permission, "barakat.overrides.company_scope.has_permission"
)

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# Presence. Both jobs iterate ONLY companies that switched wifi presence on, so a site
# with nobody enabled - `petromall`, and every company that never asked for this - does
# no work at all.
#
# The sweep has to exist: a watcher report can only ever say "I can see these devices".
# A departure is the absence of a sighting, which no incoming request can announce, so
# without a timer nobody ever leaves.
scheduler_events = {
	"cron": {
		"* * * * *": [
			"barakat.presence.tasks.sweep_departures",
		],
	},
	"daily": [
		"barakat.presence.tasks.delete_old_sightings",
	],
}

# scheduler_events = {
# 	"all": [
# 		"barakat.tasks.all"
# 	],
# 	"daily": [
# 		"barakat.tasks.daily"
# 	],
# 	"hourly": [
# 		"barakat.tasks.hourly"
# 	],
# 	"weekly": [
# 		"barakat.tasks.weekly"
# 	],
# 	"monthly": [
# 		"barakat.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "barakat.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "barakat.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------

# Frappe's tree endpoint applies NO permissions — it queries the table directly, so
# every tree view returned every row while the list view of the same doctype filtered
# correctly. On bom.iztech.net a Cashier of BOM2 saw six other shops' item groups in
# the tree and none of them in the list. One function serves every tree doctype
# (Item Group, Customer Group, Territory, Account, Cost Center, ...), so the fix
# belongs here. `get_all_nodes` resolves its tree method through
# `frappe.override_whitelisted_method`, so this covers the whole-tree load too.
override_whitelisted_methods = {
	"frappe.desk.treeview.get_children": "barakat.overrides.treeview.get_children",
}

#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "barakat.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["barakat.utils.before_request"]
# after_request = ["barakat.utils.after_request"]

# Job Events
# ----------
# before_job = ["barakat.utils.before_job"]
# after_job = ["barakat.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"barakat.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

