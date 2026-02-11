
import frappe
from frappe.utils import getdate, flt, cint, now, cstr
import csv
import os
import re

def import_bnpl_data(file_path, default_branch, import_type="BNPL Sales", skip_validation=False):
    """
    Import Data from CSV file
    """
    if not os.path.exists(file_path):
        return "Файл не найден: " + file_path

    summary = {
        "total": 0,
        "success": 0,
        "duplicates": 0,
        "errors": 0,
        "logs": []
    }

    try:
        # Detect encoding
        encoding = 'utf-8-sig'
        
        with open(file_path, mode='r', encoding=encoding) as csvfile:
            # Check delimiter
            sample = csvfile.read(1024)
            csvfile.seek(0)
            dialect = csv.Sniffer().sniff(sample)
            
            reader = csv.DictReader(csvfile, dialect=dialect)
            
            frappe.flags.in_import = True
            
            for row in reader:
                summary["total"] += 1
                try:
                    if import_type == "Импорт складских записей":
                        process_stock_entry_csv(row, default_branch, summary, skip_validation)
                    elif import_type == "Импорт клиентов":
                        process_customer_row(row, summary, skip_validation)
                    elif import_type == "Импорт поставщиков":
                        process_supplier_row(row, summary, skip_validation)
                    elif import_type == "Импорт закупок":
                        process_purchase_row(row, default_branch, summary, skip_validation)
                    elif import_type == "Импорт договоров":
                        process_row(row, default_branch, summary, skip_validation)
                    elif import_type == "Импорт платежей":
                        process_payment_row(row, summary, skip_validation)
                    else:
                        process_row(row, default_branch, summary, skip_validation)
                    
                    frappe.db.commit()
                    summary["success"] += 1
                except Exception as e:
                    frappe.db.rollback()
                    summary["errors"] += 1
                    summary["logs"].append(f"Row {summary['total']} Error: {str(e)}")
            
            frappe.flags.in_import = False
    
    except Exception as e:
        frappe.log_error(str(e), "Import Error")
        return f"Ошибка файла: {str(e)}"

    return f"Импорт завершен ({import_type}). Всего: {summary['total']}, Успешно: {summary['success']}, Дубликаты: {summary['duplicates']}, Ошибки: {summary['errors']}. \nОшибки: {'; '.join(summary['logs'][:10])}"


def process_customer_row(row, summary, skip_validation=False):
    # Customer Import Logic
    
    # 1. Name Parsing
    full_name = row.get("Название клиента", "").strip()
    if not full_name: 
        summary["errors"] += 1
        return # Skip empty names
    
    parts = full_name.split()
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""
    middle_name = parts[2] if len(parts) > 2 else ""
    
    # 2. Duplicate Check (Phone or Name)
    # Collect all phones (split comma/semicolon-separated into separate numbers)
    phones = []
    for col in ["Телефон 1", "Телефон 2", "Телефон"]:
        for p in parse_phone_list(row.get(col, "")):
            if p not in phones:
                phones.append(p)
    
    # Check if customer exists by phone
    existing_customer = None
    for p in phones:
        existing = frappe.db.get_value("Customer Phone Number", {"phone_number": p}, "parent")
        if existing:
            existing_customer = frappe.get_doc("Customer Profile", existing)
            summary["duplicates"] += 1
            # Update existing? For now, let's update empty fields
            break
    
    if existing_customer:
        customer = existing_customer
    else:
        customer = frappe.new_doc("Customer Profile")
        customer.first_name = first_name
        customer.last_name = last_name
        customer.middle_name = middle_name
        customer.status = "Active"

    # 3. Update details
    
    # Passport
    passport_raw = row.get("Серия паспорта", "").strip() # e.g. AD8539218
    if passport_raw:
        # Regex to split AA 1234567 or AA1234567
        match = re.match(r"([A-Z]{2})\s*(\d+)", passport_raw.upper())
        if match:
            customer.passport_series = match.group(1)
            customer.passport_number = match.group(2)
        else:
            customer.passport_number = passport_raw

    # Date of Birth
    dob = row.get("Дата рожд.", "").strip()
    if dob:
        customer.date_of_birth = parse_date(dob)

    # Address
    address = row.get("Адрес", "").strip()
    if address:
        customer.current_address = address
        # customer.registration_address = address # Assuming same if only one given

    # Workplace
    workplace = row.get("Иш жойи", "").strip()
    if workplace:
        customer.workplace = workplace

    # Gender
    gender_map = {"M": "Мужской", "F": "Женский"} # Adjust if source has different codes
    # User source has "Мужской" ? No sample has empty gender. 
    # Let's assume default mapping or skip if empty.

    # Phone Numbers (each entry in phones is already cleaned; add separately)
    if not existing_customer:
        for i, p in enumerate(phones):
            customer.append("phone_numbers", {
                "phone_number": p,
                "is_primary": 1 if i == 0 else 0
            })
            
    customer.flags.ignore_permissions = True
    if skip_validation:
        customer.flags.ignore_validate = True
        customer.flags.ignore_mandatory = True
        
    customer.save()


def process_row(row, default_branch, summary, skip_validation=False):
    # BNPL Sales Import Logic (Existing)
    # Map CSV fields (Russian keys)
    doc_number = row.get("Номер документа", "").strip()
    if not doc_number:
        return # Skip empty rows

    # Check for duplicate by Document Number
    if frappe.db.exists("Sales Order", {"po_no": doc_number}):
        summary["duplicates"] += 1
        raise Exception(f"Duplicate Document Number: {doc_number}")

    # 1. Create/Get Customer (phone may be comma-separated)
    client_name = row.get("Клиент", "").strip()
    phone_raw = row.get("Телефон", "").strip()
    customer = get_or_create_customer(client_name, phone_raw, skip_validation)

    # 2. Create/Get Product
    product_name = row.get("Наименование товара", "").strip()
    product_code = row.get("Код товара", "").strip()
    imei = row.get("IMEI", "").strip()
    price = parse_number(row.get("Цена продажи", "0"))
    product = get_or_create_product(product_name, product_code, imei, price, skip_validation)

    # 3. Create Sales Order
    sale_date = parse_date(row.get("Дата продажи", ""))
    total_amount = parse_number(row.get("Общая сумма", "0"))
    paid_amount = parse_number(row.get("Оплачено", "0"))
    remaining_debt = parse_number(row.get("Остаток долга", "0"))
    
    so = frappe.new_doc("Sales Order")
    so.customer = customer.name
    so.transaction_date = sale_date
    so.delivery_date = sale_date
    so.company = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company") or "My Company"
    so.branch = default_branch
    so.branch = default_branch
    # Store legacy doc number in notes for tracing
    so.notes = f"Legacy ID: {doc_number}"
    so.po_no = doc_number
    
    so.append("items", {
        "product": product.name,
        "product_name": product.product_name,
        "quantity": 1,
        "unit_price": price, 
        "amount": price
    })
    
    so.flags.ignore_permissions = True
    if skip_validation:
        so.flags.ignore_validate = True
        so.flags.ignore_mandatory = True
        
    so.insert()
    so.submit()

    # 4. Create Installment Plan if there is debt
    if remaining_debt > 0:
        create_installment_plan(so, customer, row, sale_date, total_amount, paid_amount, remaining_debt)


def get_or_create_customer(name, phone, skip_validation=False):
    # BNPL Logic helper; phone may be comma/semicolon-separated
    phones = parse_phone_list(phone) if phone else []
    if phones:
        for p in phones:
            existing = frappe.db.get_value("Customer Phone Number", {"phone_number": p}, "parent")
            if existing:
                return frappe.get_doc("Customer Profile", existing)
    
    parts = name.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    customer = frappe.new_doc("Customer Profile")
    customer.first_name = first_name
    customer.last_name = last_name
    customer.status = "Active"
    
    for i, p in enumerate(phones):
        customer.append("phone_numbers", {
            "phone_number": p,
            "is_primary": 1 if i == 0 else 0
        })

    customer.flags.ignore_permissions = True
    if skip_validation:
        customer.flags.ignore_validate = True
        customer.flags.ignore_mandatory = True
        
    customer.insert()
    return customer


def get_or_create_product(name, code, imei, price, skip_validation=False):
    if code and frappe.db.exists("Product", {"product_code": code}):
         return frappe.get_doc("Product", {"product_code": code})
         
    # Check Item too just in case
    # if code and frappe.db.exists("Item", code):
    #    return frappe.get_doc("Item", code)
    
    item = frappe.new_doc("Product")
    item.product_name = name
    item.product_code = code or frappe.generate_hash(length=8)
    item.selling_price = price
    item.product_cost = 0 # Mandatory field
    
    item.flags.ignore_permissions = True
    if skip_validation:
        item.flags.ignore_validate = True
        item.flags.ignore_mandatory = True
        
    item.insert()
    return item

def create_installment_plan(so, customer, row, sale_date, total_amount, paid_amount, remaining_debt):
    payment_count_raw = row.get("Количество платежей", "0")
    if not payment_count_raw: payment_count_raw = "0"
    
    payment_count = cint(payment_count_raw)
    num_installments = payment_count - 1
    if num_installments < 1:
        num_installments = 1

    plan = frappe.new_doc("Installment Plan")
    plan.customer = customer.name
    plan.sales_order = so.name
    plan.start_date = sale_date
    plan.status = "Active"
    
    plan.principal_amount = total_amount
    plan.down_payment = paid_amount
    plan.financed_amount = remaining_debt
    plan.number_of_installments = num_installments
    plan.installment_amount = remaining_debt / num_installments if num_installments > 0 else remaining_debt
    plan.frequency = "Monthly"
    
    plan.flags.ignore_permissions = True
    plan.insert()
    plan.submit()


def parse_number(val):
    if not val: return 0.0
    val = str(val).replace(" ", "").replace(",", ".").strip()
    if not val: return 0.0
    try:
        return flt(val)
    except:
        return 0.0

def parse_date(val):
    if not val: return now()
    # Handle DD.MM.YY or DD.MM.YYYY
    try:
        val = val.strip()
        parts = val.split(".")
        if len(parts) == 3:
            day = parts[0]
            month = parts[1]
            year = parts[2]
            if len(year) == 2: year = "20" + year
            if len(day) == 1: day = "0" + day
            if len(month) == 1: month = "0" + month
            return f"{year}-{month}-{day}"
    except:
        pass
    return now()

def clean_phone(val):
    if not val: return ""
    return re.sub(r"[^0-9+]", "", val)


def parse_phone_list(raw):
    """Split comma/semicolon-separated phone string into list of non-empty cleaned numbers (unique)."""
    if not raw or not str(raw).strip():
        return []
    seen = set()
    result = []
    for part in re.split(r"[,;]", str(raw)):
        p = part.strip()
        if not p:
            continue
        cleaned = clean_phone(p)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def process_supplier_row(row, summary, skip_validation=False):
    name = row.get("Название клиента", "").strip() or row.get("Name", "").strip()
    if not name: return

    # Check existence
    if frappe.db.exists("Supplier", {"supplier_name": name}):
        summary["duplicates"] += 1
        return

    doc = frappe.new_doc("Supplier")
    doc.supplier_name = name
    
    phone = row.get("Телефон 1", "").strip() or row.get("Телефон", "").strip()
    if phone:
        doc.mobile_no = clean_phone(phone)
        
    doc.flags.ignore_permissions = True
    if skip_validation:
        doc.flags.ignore_validate = True
        doc.flags.ignore_mandatory = True
        
    doc.insert()

def process_purchase_row(row, default_branch, summary, skip_validation=False):
    # Purchase Import -> Stock Entry (Receive)
    supplier_name = row.get("Поставщик", "").strip()
    
    # Ensure Supplier exists (custom doctype)
    if supplier_name and not frappe.db.exists("Supplier", {"supplier_name": supplier_name}):
        s = frappe.new_doc("Supplier")
        s.supplier_name = supplier_name
        s.flags.ignore_permissions = True
        s.insert()
    
    pi_date = parse_date(row.get("Дата покупки", ""))
    
    item_code = row.get("Код товара", "").strip()
    item_name = row.get("Махсулот", "").strip()
    rate = parse_number(row.get("Таннарх", "0"))
    imei = row.get("Имейка", "").strip()
    
    # Ensure Product exists
    product = get_or_create_product(item_name, item_code, imei, rate)
    
    # Create Stock Entry
    se = frappe.new_doc("Stock Entry")
    se.entry_type = "Поступление" # Receipt
    se.posting_date = pi_date
    se.posting_time = now()
    
    # Assign Warehouse - IMPORTANT: Need a default. 
    # Attempt to use 'default_branch' linked Warehouse if possible, or fallback.
    se.warehouse = frappe.db.get_value("Warehouse", {"branch": default_branch}, "name")
    if not se.warehouse:
        # Fallback to first available warehouse
        se.warehouse = frappe.db.get_value("Warehouse", {}, "name")
        
    se.remarks = f"Imported Purchase from Supplier: {supplier_name}"
    
    se.append("items", {
        "product": product.name,
        "product_name": product.product_name,
        "quantity": 1,
        "rate": rate,
        "amount": rate,
        "serial_no": imei
    })
    
    se.flags.ignore_permissions = True
    if skip_validation:
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True
        
    se.insert()
    se.submit()

def process_payment_row(row, summary, skip_validation=False):
    # Payment Import -> Payment Transaction
    doc_num = row.get("Номер документа", "").strip()
    amount = parse_number(row.get("Всего оплачено", "0"))
    if not amount:
        amount = parse_number(row.get("Оплачено", "0"))
        
    if not doc_num or amount <= 0:
        return

    # Find linked Sales Order by checking notes for legacy ID or assuming PO No if it existed
    # Since SalesOrder doesn't have po_no, we search using LIKE logic in notes if we stored it there
    # OR we assume the user mapped it manually. 
    # Wait, process_row below saves 'po_no'    # Find linked Sales Order
    so_name = frappe.db.get_value("Sales Order", {"po_no": doc_num}, "name")
    
    if not so_name:
        # Fallback: Check notes for Legacy ID
        so_name = frappe.db.get_value("Sales Order", {"notes": ["like", f"%Legacy ID: {doc_num}%"]}, "name")

    if not so_name:
        summary["errors"] += 1
        summary["logs"].append(f"Ошибка платежа: Sales Order не найден для {doc_num}")
        return

    # Create Payment Transaction
    pe = frappe.new_doc("Payment Transaction")
    pe.payment_date = now() # Or parsed date
    pe.amount = amount
    pe.status = "Completed"
    pe.payment_method = "Cash"
    
    # Link to Sales Order
    pe.reference_doctype = "Sales Order"
    pe.reference_name = so_name
    
    # Customer
    customer = frappe.db.get_value("Sales Order", so_name, "customer")
    pe.customer = customer
    
    pe.flags.ignore_permissions = True
    if skip_validation:
        pe.flags.ignore_validate = True
        pe.flags.ignore_mandatory = True
        
    pe.insert()
    # Payment Transaction might not need submit if not submittable, but check doc definition. 
    # It is NOT submittable based on field 'is_submittable'.

def process_stock_entry_csv(row, default_branch, summary, skip_validation=False):
    # Stock Entry Import
    # CSV: Код товара,Наименование товара,Состояние,Цвет,Бренд,Серийный номер
    
    code = row.get("Код товара", "").strip()
    name = row.get("Наименование товара", "").strip()
    serial_no = row.get("Серийный номер", "").strip()
    
    if not code and not name:
        return

    # attributes for description
    condition = row.get("Состояние", "").strip()
    color = row.get("Цвет", "").strip()
    brand = row.get("Бренд", "").strip()
    
    # 1. Get/Create Product
    product = None
    if code:
        if frappe.db.exists("Product", {"product_code": code}):
            product = frappe.get_doc("Product", {"product_code": code})
    
    if not product:
        # Create new
        product = frappe.new_doc("Product")
        product.product_name = name or code
        product.product_code = code
        product.selling_price = 0 # Default
        product.product_cost = 0 # Mandatory field
        
        # Add attributes to description
        desc_parts = []
        if brand: desc_parts.append(f"Бренд: {brand}")
        if color: desc_parts.append(f"Цвет: {color}")
        if condition: desc_parts.append(f"Состояние: {condition}")
        
        if desc_parts:
            product.description = ", ".join(desc_parts)
            
        product.category = "Smartphones" # Default
        if "MacBook" in name or "iMac" in name:
            product.category = "Laptops" if "MacBook" in name else "Computers"

        # Ensure category exists
        if not frappe.db.exists("Product Category", product.category):
            cat = frappe.new_doc("Product Category")
            cat.category_name = product.category
            cat.flags.ignore_permissions = True
            cat.insert()
        
        product.flags.ignore_permissions = True
        product.insert()
    
    # 2. Create Stock Entry (Material Receipt)
    # We create one Stock Entry per Row? Or should we aggregate?
    # For simplicity and robust error handling, one SE per row is safer, but data intensive.
    # Let's create one SE per row as per previous patterns.
    
    se = frappe.new_doc("Stock Entry")
    se.entry_type = "Поступление" # Material Receipt
    se.posting_date = now()
    
    # Warehouse
    if default_branch:
        warehouse = frappe.db.get_value("Warehouse", {"branch": default_branch}, "name")
        if warehouse:
            se.warehouse = warehouse
            
    if not se.warehouse:
        # Find any warehouse
        se.warehouse = frappe.db.get_value("Warehouse", {}, "name")
        
    se.remarks = f"Imported Stock: {name} ({serial_no})"
    
    se.append("items", {
        "product": product.name,
        "quantity": 1,
        "serial_no": serial_no,
        "rate": 0,
        "amount": 0
    })
    
    se.flags.ignore_permissions = True
    if skip_validation:
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True
        
    se.insert()
    se.submit()
