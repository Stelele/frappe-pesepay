app_name = "pesepay"
app_title = "Pesepay"
app_publisher = "Gift Mugweni"
app_description = "A Pesepay gateway implementation allowing for Zimbabwean processing of Visa, Mastercard, Ecocash, InnBucks, Omari, etc"
app_email = "giftmugweni@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["frappe", "payments"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pesepay",
# 		"logo": "/assets/pesepay/logo.png",
# 		"title": "Pesepay",
# 		"route": "/pesepay",
# 		"has_permission": "pesepay.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pesepay/css/pesepay.css"
# app_include_js = "/assets/pesepay/js/pesepay.js"

# include js, css files in header of web template
# web_include_css = "/assets/pesepay/css/pesepay.css"
# web_include_js = "/assets/pesepay/js/pesepay_checkout.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pesepay/public/scss/website"

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
# app_include_icons = "pesepay/public/icons.svg"

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
# 	"methods": "pesepay.utils.jinja_methods",
# 	"filters": "pesepay.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pesepay.install.before_install"
after_install = "pesepay.installer.after_install"

# Uninstallation
# ------------

before_uninstall = "pesepay.installer.before_uninstall"
# after_uninstall = "pesepay.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pesepay.utils.before_app_install"
# after_app_install = "pesepay.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pesepay.utils.before_app_uninstall"
# after_app_uninstall = "pesepay.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pesepay.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pesepay.notifications.get_notification_config"

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
# doc_events to add if needed - keep commented for now

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": [
		"pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_pending_payments",
	],
}

# Testing
# -------

# before_tests = "pesepay.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pesepay.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pesepay.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pesepay.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pesepay.utils.before_request"]
# after_request = ["pesepay.utils.after_request"]

# Job Events
# ----------
# before_job = ["pesepay.utils.before_job"]
# after_job = ["pesepay.utils.after_job"]

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
# 	"pesepay.auth.validate"
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

