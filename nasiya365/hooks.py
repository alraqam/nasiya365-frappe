"""
Nasiya365 Hooks Configuration
Defines app metadata, scheduled jobs, and system hooks
"""

app_name = "nasiya365"
app_title = "Nasiya365"
app_publisher = "Nasiya365 Team"
app_description = "BNPL SaaS Platform - Buy Now Pay Later with Inventory & Sales Management"
app_email = "info@nasiya365.uz"
app_license = "MIT"
app_version = "0.0.1"

# Required apps for this app
required_apps = ["frappe"]

# Includes in <head>
# ------------------

# Include JS in all pages
app_include_js = [
    "/assets/nasiya365/js/installment_calculator.js"
]

# Include CSS in all pages
# app_include_css = "/assets/nasiya365/css/nasiya365.css"

# Include JS in web pages
# web_include_js = "/assets/nasiya365/js/nasiya365-web.js"

# Include CSS in web pages
# web_include_css = "/assets/nasiya365/css/nasiya365-web.css"

# Include JS in desk pages
# doctype_js = {
#     "Sales Order": "public/js/sales_order.js"
# }

# Include list JS for DocTypes
# doctype_list_js = {}

# Include tree JS for DocTypes
# doctype_tree_js = {}

# Home Pages
# ----------

# Application home page (for logged-in users)
# home_page = "nasiya365_dashboard"

# Website home page
# website_route_rules = []

# Generators
# ----------

# Automatically create pages for DocTypes
# website_generators = ["Web Page"]

# Jinja Template Filters
# ----------------------

# Add custom Jinja filters for templates
jinja = {
    "methods": [
        "nasiya365.utils.jinja_filters.currency_format",
        "nasiya365.utils.jinja_filters.date_format",
    ]
}

# Installation
# ------------

# before_install = "nasiya365.install.before_install"
after_install = "nasiya365.install.after_install"

# Uninstallation
# --------------

# before_uninstall = "nasiya365.uninstall.before_uninstall"
# after_uninstall = "nasiya365.uninstall.after_uninstall"

# DocType Class Overrides
# -----------------------

# Override standard doctype classes
# override_doctype_class = {
#     "ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------

# Document Events
# ---------------

# Hook on document methods and events
doc_events = {
}

# Scheduled Tasks
# ---------------

scheduler_events = {
    # Run every day at midnight
    "daily": [
        "nasiya365.tasks.daily.check_overdue_installments",
        "nasiya365.tasks.daily.send_payment_reminders",
    ],
    # Run every hour
    "hourly": [
        "nasiya365.tasks.hourly.sync_payment_status",
    ],
    # Run every week
    "weekly": [
        "nasiya365.tasks.weekly.generate_collection_report",
    ],
    # Cron-style scheduling
    "cron": {
        # Every day at 9 AM - send payment reminders
        "0 9 * * *": [
            "nasiya365.tasks.notifications.send_due_today_reminders"
        ],
        # Every day at 6 PM - send overdue warnings
        "0 18 * * *": [
            "nasiya365.tasks.notifications.send_overdue_warnings"
        ],
    },
}

# Testing
# -------

# before_tests = "nasiya365.install.before_tests"

# Override Methods
# ----------------

# override_whitelisted_methods = {}

# Fixtures
# --------

# Export custom settings and masters during install
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [["module", "=", "Nasiya365"]]
    },
    {
        "doctype": "Property Setter",
        "filters": [["module", "=", "Nasiya365"]]
    },
]

# Permissions
# -----------

# Permission query conditions
# permission_query_conditions = {
#     "Sales Order": "nasiya365.permissions.sales_order_permission_query",
# }

# has_permission = {
#     "Sales Order": "nasiya365.permissions.has_sales_order_permission",
# }

# DocType User Permissions
# ------------------------

# User Data Protection
# --------------------

# user_data_fields = [
#     {
#         "doctype": "Customer Profile",
#         "filter_by": "email",
#         "redact_fields": ["phone", "passport_number"],
#         "partial": 1,
#     },
# ]

# Authentication and Authorization
# --------------------------------

# auth_hooks = [
#     "nasiya365.auth.validate_session"
# ]

# Automatically update python dependencies
# -----------------------------------------

# after_sync = "nasiya365.install.after_sync"

# Boot Session Info
# -----------------

# boot_session = "nasiya365.boot.boot_session"

# PDF Print Formats
# -----------------

# Use custom PDF generation with WeasyPrint
# pdf_generator = "nasiya365.utils.pdf.generate_pdf"

# Translation
# -----------

# Translate table data
# translate_doctype = ["Print Template"]
