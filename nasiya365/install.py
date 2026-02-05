"""
Installation Scripts for Nasiya365
"""

import frappe
from frappe import _


def after_install():
    """Run after app installation"""
    create_default_roles()
    create_default_print_templates()
    frappe.db.commit()
    print("Nasiya365 installed successfully!")


def create_default_roles():
    """Create default roles for Nasiya365"""
    roles = [
        {
            "role_name": "Nasiya365 Admin",
            "desk_access": 1,
            "description": "Full access to all Nasiya365 features"
        },
        {
            "role_name": "Branch Manager",
            "desk_access": 1,
            "description": "Manage branch operations and staff"
        },
        {
            "role_name": "Salesperson",
            "desk_access": 1,
            "description": "Create sales and BNPL applications"
        },
        {
            "role_name": "Cashier",
            "desk_access": 1,
            "description": "Process payments and cash receipts"
        },
        {
            "role_name": "Collector",
            "desk_access": 1,
            "description": "Follow up on overdue payments"
        },
        {
            "role_name": "Credit Officer",
            "desk_access": 1,
            "description": "Approve or reject credit applications"
        },
        {
            "role_name": "Warehouse Manager",
            "desk_access": 1,
            "description": "Manage inventory and stock movements"
        },
    ]
    
    for role_data in roles:
        if not frappe.db.exists("Role", role_data["role_name"]):
            role = frappe.new_doc("Role")
            role.role_name = role_data["role_name"]
            role.desk_access = role_data.get("desk_access", 1)
            role.insert(ignore_permissions=True)
            print(f"Created role: {role_data['role_name']}")


def create_default_print_templates():
    """Create default print templates"""
    # Templates will be created when Print Template DocType is ready
    pass
