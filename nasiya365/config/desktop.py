"""
Desktop Configuration for Nasiya365
Defines shortcuts and icons for the Frappe Desk
"""

from frappe import _


def get_data(user=None):
    return [
        {
            "module_name": "Nasiya365",
            "color": "#2E7D32",
            "icon": "octicon octicon-credit-card",
            "type": "module",
            "label": _("Nasiya365"),
            "description": _("Платформа рассрочки (BNPL)"),
        }
    ]
