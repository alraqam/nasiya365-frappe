import frappe

ws = frappe.get_doc('Workspace', 'Nasiya365')
ws.is_hidden = 0
ws.public = 1
ws.module = 'Nasiya365'
frappe.db.sql("DELETE FROM `tabWorkspace` WHERE for_user = 'Administrator' AND label = 'Nasiya365'")
ws.save(ignore_permissions=True)
frappe.db.commit()
frappe.clear_cache()
print('✓ Workspace Nasiya365 is now visible!')
