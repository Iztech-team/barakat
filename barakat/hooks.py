app_name = "barakat"
app_title = "Barakat"
app_publisher = "Osama Tbaileh"
app_description = "Barakat custom ERP configuration and extensions"
app_email = "osamatbaileh@iztechvalley.ps"
app_license = "mit"

fixtures = [
	{
		"dt": "DocType",
		"filters": [["name", "in", ["POS Employee Branch"]]],
	},
	{
		"dt": "Custom Field",
		"filters": [
			[
				"fieldname",
				"in",
				[
					"custom_pos_company",
					"custom_pos_warehouse",
					"custom_pos_cost_center",
					"custom_pos_profile",
					"custom_pos_price_list",				"custom_cash_account",
				"custom_counter_account",					"custom_pos_pin",
					"custom_role_preset",
					"custom_pos_branches",
					"custom_external_id",
					"custom_operator_employee",				"custom_pos_opening_entry",					"custom_device_id",
					"custom_opened_by_staff",
					"custom_closed_by_staff",
				],
			]
		],
	},
]

after_install = "barakat.setup.install.after_install"

doc_events = {
	"Employee": {
		"validate": "barakat.validations.validate_employee_pin",
	},
}

doctype_js = {
	"Employee": "public/js/employee.js",
}

override_doctype_class = {
	"POS Opening Entry": "barakat.overrides.pos_opening_entry.BarakatPOSOpeningEntry"
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

