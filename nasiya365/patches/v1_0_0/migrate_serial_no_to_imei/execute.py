import frappe


def execute():
    """Copy serial_no → imei for all Stock Entry Items where imei is empty."""
    frappe.db.sql(
        """UPDATE `tabStock Entry Item`
           SET imei = serial_no
           WHERE IFNULL(TRIM(imei), '') = ''
             AND IFNULL(TRIM(serial_no), '') != ''"""
    )
