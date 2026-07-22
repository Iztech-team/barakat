app_name = "barakat"
app_title = "Barakat"
app_publisher = "Osama Tbaileh"
app_description = "Barakat custom ERP configuration and extensions"
app_email = "osamatbaileh@iztechvalley.ps"
app_license = "mit"

fixtures = [
	{
		"dt": "DocType",
		"filters": [["name", "in", ["POS Employee Branch", "Device", "Branch POS Profile"]]],
	},
	{
		"dt": "Role",
		"filters": [
			[
				"name",
				"in",
				[
					"Branch Supervisor",
					"Cashier",
					"Accountant",
					"Inventory Keeper",
					"HR",
					# Dedicated role granting read+write on System Settings /
					# Global Defaults only (rounding page). Bundled into the
					# Manager persona by the proxy; perms set by
					# barakat.setup.install._grant_settings_manager_perms.
					"Barakat Settings Manager",
					"Barakat Staff Manager",
					# Narrow roles behind the persona allow-list. Each exists
					# because no native ERPNext role covers the capability
					# without also handing out far more — see barakat/permissions.py.
					# Perms set by barakat.setup.install._grant_barakat_role_perms.
					"Barakat POS Operator",
					"Barakat Attendance Manager",
					"Barakat Loyalty Manager",
					"Barakat Loyalty Viewer",
					"Barakat Currency Manager",
					"Barakat Salary Viewer",
					"Barakat Purchase Invoice Clerk",
					"Barakat Payment Mode Manager",
					"Barakat Customer Manager",
					"Barakat Cashier Reader",
				],
			]
		],
	},
	{
		"dt": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				[
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
		"validate": [
			"barakat.validations.validate_employee_pin",
			"barakat.overrides.staff_roles.guard_role_preset",
		],
		"after_insert": "barakat.overrides.staff_roles.reassert_persona_roles",
		"on_update": "barakat.overrides.staff_roles.reassert_persona_roles",
	},
	"Branch": {
		"validate": "barakat.overrides.branch.validate_branch",
	},
	"POS Profile": {
		"validate": "barakat.validations.validate_pos_profile_accounts",
	},
	"Item": {
		"validate": "barakat.validations.validate_item_disable",
	},
	"Customer": {
		"validate": "barakat.validations.validate_customer_mobile_unique",
	},
}

doctype_js = {
	"Employee": "public/js/employee.js",
	"POS Profile": "public/js/pos_profile.js",
	"Item": "public/js/item.js",
}

override_doctype_class = {
	"POS Opening Entry": "barakat.overrides.pos_opening_entry.BarakatPOSOpeningEntry",
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

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

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
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "barakat.event.get_events"
# }
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

