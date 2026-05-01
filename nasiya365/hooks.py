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

# Open the Nasiya365 *Workspace* (not a Page) so the left sidebar shows workspace links / Card Breaks.
# Slug = frappe.router.slug("Nasiya365") -> "nasiya365". Dashboard page: first link inside workspace.
app_home = "/app/nasiya365"

# Required apps for this app
required_apps = ["frappe"]

# Includes in <head>
# ------------------

# Include JS in all pages
app_include_js = [
    "/assets/nasiya365/js/installment_calculator.js",
    "/assets/nasiya365/js/pwa_register.js",
    "/assets/nasiya365/js/list_column_picker.js",
]

# Include CSS in all pages
app_include_css = [
    "/assets/nasiya365/css/bnpl_control_center.css",
    "/assets/nasiya365/css/installment_plan_wizard.css",
]

# Include JS in web pages (login, portal) so PWA manifest/SW are available before desk
web_include_js = ["/assets/nasiya365/js/pwa_register.js"]

# Include CSS in web pages
# web_include_css = "/assets/nasiya365/css/nasiya365-web.css"

# Include JS in desk pages
doctype_js = {
    "Sales Order": "public/js/sales_order.js",
    "Payment Transaction": "public/js/payment_transaction.js",
    "Installment Plan": "public/js/installment_plan.js",
    "Cashbox": "public/js/cashbox.js",
    "Cash Handover": "public/js/cash_handover.js",
}

doctype_list_js = {
    "Sales Order": "public/js/sales_order_list.js",
    "Installment Plan": "public/js/installment_plan_list.js",
}

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
after_migrate = "nasiya365.install.after_migrate"

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

# Hook on document methods and events
doc_events = {
    "Payment Transaction": {
        "after_insert": "nasiya365.payment_doc_events.payment_transaction_after_insert",
        "on_update": "nasiya365.payment_doc_events.payment_transaction_on_update",
    },
    "Product": {
        "before_validate": "nasiya365.payment_doc_events.product_before_validate",
    },
    "Merchant Settings": {
        "on_update": "nasiya365.payment_doc_events.merchant_settings_on_update",
    },
    "Installment Plan": {
        "after_insert": "nasiya365.payment_doc_events.installment_plan_refresh_stock_entry",
        "on_update": "nasiya365.payment_doc_events.installment_plan_refresh_stock_entry",
        "on_cancel": "nasiya365.payment_doc_events.installment_plan_refresh_stock_entry",
    },
    "Sales Order": {
        "on_submit": "nasiya365.payment_doc_events.sales_order_refresh_stock_entries",
        "on_cancel": "nasiya365.payment_doc_events.sales_order_refresh_stock_entries",
    },
    "Branch": {
        "on_update": "nasiya365.permissions.clear_branch_user_cache",
    },
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
    {
        "doctype": "Print Format",
        "filters": [["module", "=", "nasiya365"]]
    },
]

# Permissions
# -----------

# Permission query conditions
permission_query_conditions = {
    "Sales Order": "nasiya365.permissions.sales_order_query",
    "Installment Plan": "nasiya365.permissions.installment_plan_query",
    "Collector": "nasiya365.permissions.collector_query",
    "Cashbox": "nasiya365.permissions.cashbox_query",
    "Cash Handover": "nasiya365.permissions.cash_handover_query",
    "Expense": "nasiya365.permissions.expense_query",
    "Warehouse": "nasiya365.permissions.warehouse_query",
    "Payment Transaction": "nasiya365.permissions.payment_transaction_query",
    "Customer Profile": "nasiya365.permissions.customer_profile_query",
    "Stock Entry": "nasiya365.permissions.stock_entry_query",
}

has_permission = {
    "Sales Order": "nasiya365.permissions.has_sales_order_permission",
    "Installment Plan": "nasiya365.permissions.has_installment_plan_permission",
    "Collector": "nasiya365.permissions.has_collector_permission",
    "Cashbox": "nasiya365.permissions.has_cashbox_permission",
    "Cash Handover": "nasiya365.permissions.has_cash_handover_permission",
    "Expense": "nasiya365.permissions.has_expense_permission",
    "Warehouse": "nasiya365.permissions.has_warehouse_permission",
    "Payment Transaction": "nasiya365.permissions.has_payment_transaction_permission",
    "Customer Profile": "nasiya365.permissions.has_customer_profile_permission",
    "Stock Entry": "nasiya365.permissions.has_stock_entry_permission",
}

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

on_session_creation = "nasiya365.permissions.auto_assign_manager_branch"

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
