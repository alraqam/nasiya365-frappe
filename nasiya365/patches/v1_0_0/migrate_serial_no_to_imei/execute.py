import frappe


def execute():
    """Copy serial_no → imei for all Stock Entry Items where imei is empty."""
    if not frappe.db.has_column("Stock Entry Item", "imei"):
        frappe.db.sql(
            "ALTER TABLE `tabStock Entry Item` ADD COLUMN `imei` varchar(140) DEFAULT NULL"
        )

    frappe.db.sql(
        """UPDATE `tabStock Entry Item`
           SET imei = serial_no
           WHERE IFNULL(TRIM(imei), '') = ''
             AND IFNULL(TRIM(serial_no), '') != ''"""
    )
