import frappe
from nasiya365.data_import import import_bnpl_data

def execute():
    # Path to the file inside the container
    # We mounted ./nasiya365 to /home/frappe/frappe-bench/apps/nasiya365
    # So stock_entry.csv (which is in nasiya365 root locally) should be at...
    # Wait, I copied it to c:\zOSPanel\domains\nasiya365-frappe\nasiya365\stock_entry.csv
    # The docker-compose mounts: ./nasiya365:/home/frappe/frappe-bench/apps/nasiya365
    # So it should be at /home/frappe/frappe-bench/apps/nasiya365/stock_entry.csv
    
    file_path = "/home/frappe/frappe-bench/apps/nasiya365/stock_entry.csv"
    
    # We need a branch. Let's find one.
    branch = frappe.db.get_value("Branch", {}, "name")
    if not branch:
        print("No Branch found! Creating a default one.")
        b = frappe.new_doc("Branch")
        b.insert()
        branch = b.name
        
    # Ensure Warehouse exists
    warehouse = frappe.db.get_value("Warehouse", {"branch": branch}, "name")
    if not warehouse:
        print("No Warehouse for branch! Creating one.")
        w = frappe.new_doc("Warehouse")
        w.warehouse_name = f"Warehouse - {branch}"
        w.branch = branch
        w.insert()
        warehouse = w.name
        
    print(f"Starting import using Branch: {branch} (Warehouse: {warehouse})")
    
    result = import_bnpl_data(
        file_path=file_path,
        default_branch=branch,
        import_type="Импорт складских записей",
        skip_validation=True
    )
    
    print(result)
