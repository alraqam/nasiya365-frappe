"""
Jinja Template Filters for Nasiya365
Custom filters for PDF and print templates
"""

import frappe
from frappe.utils import fmt_money, formatdate


def currency_format(value, currency="UZS"):
    """Format a number as currency
    
    Usage in templates: {{ amount | currency_format }}
    """
    if value is None:
        return "0 UZS"
    
    try:
        # Format with thousands separator
        formatted = "{:,.0f}".format(float(value))
        return f"{formatted} {currency}"
    except (ValueError, TypeError):
        return str(value)


def date_format(value, format_string="%d.%m.%Y"):
    """Format a date for display
    
    Usage in templates: {{ date | date_format }}
    """
    if not value:
        return ""
    
    try:
        if isinstance(value, str):
            from datetime import datetime
            value = datetime.strptime(value, "%Y-%m-%d")
        return value.strftime(format_string)
    except (ValueError, TypeError):
        return str(value)


def phone_format(value):
    """Format Uzbek phone numbers
    
    Usage in templates: {{ phone | phone_format }}
    """
    if not value:
        return ""
    
    # Remove all non-digits
    digits = ''.join(filter(str.isdigit, str(value)))
    
    # Format as +998 XX XXX-XX-XX
    if len(digits) == 12 and digits.startswith("998"):
        return f"+{digits[:3]} {digits[3:5]} {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
    elif len(digits) == 9:
        return f"+998 {digits[:2]} {digits[2:5]}-{digits[5:7]}-{digits[7:9]}"
    
    return value


def passport_format(series, number):
    """Format passport number
    
    Usage in templates: {{ passport_format(passport_series, passport_number) }}
    """
    if not series or not number:
        return ""
    return f"{series.upper()} {number}"
