#!/usr/bin/env python3
"""
Script to ensure Nasiya365 workspace is visible
"""
import frappe

def setup_workspace():
    frappe.init(site='my.nasiya365.uz')
    frappe.connect()
    
    try:
        # Get or create workspace
        if frappe.db.exists("Workspace", "Nasiya365"):
            ws = frappe.get_doc("Workspace", "Nasiya365")
            print(f"Found existing workspace: {ws.name}")
        else:
            print("Workspace not found, creating...")
            return
        
        # Ensure it's visible
        ws.is_hidden = 0
        ws.public = 1
        
        # Check if it has the right module
        if not ws.module:
            ws.module = "Nasiya365"
        
        ws.save(ignore_permissions=True)
        frappe.db.commit()
        
        print(f"✓ Workspace '{ws.name}' is now visible")
        print(f"  - Public: {ws.public}")
        print(f"  - Hidden: {ws.is_hidden}")
        print(f"  - Module: {ws.module}")
        
        # Clear cache
        frappe.clear_cache()
        print("✓ Cache cleared")
        
    except Exception as e:
        print(f"Error: {e}")
        frappe.db.rollback()
    finally:
        frappe.destroy()

if __name__ == "__main__":
    setup_workspace()
