
import frappe
from frappe.utils import getdate, flt, cint, now, cstr
import csv
import os
import re


class SkipRow(Exception):
    """Raised when a row should be skipped (empty/invalid) without counting as success or error."""
    pass


def _create_savepoint(name):
    """Create a savepoint so we can roll back only the current row on failure."""
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "", name)[:64]
    if safe_name:
        frappe.db.sql(f"SAVEPOINT `{safe_name}`")


def _rollback_to_savepoint(name):
    """Roll back to savepoint (current row only); then commit so next row starts clean."""
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "", name)[:64]
    if safe_name:
        try:
            frappe.db.sql(f"ROLLBACK TO SAVEPOINT `{safe_name}`")
        except Exception:
            frappe.db.rollback()
    else:
        frappe.db.rollback()


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
        "skipped": 0,
        "logs": []
    }

    # Set import flag to skip certain hooks during legacy data import
    frappe.flags.in_import = True

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
                sp_name = f"import_row_{summary['total']}"
                try:
                    _create_savepoint(sp_name)
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
                except SkipRow:
                    _rollback_to_savepoint(sp_name)
                    summary["skipped"] += 1
                except Exception as e:
                    _rollback_to_savepoint(sp_name)
                    summary["errors"] += 1
                    summary["logs"].append(f"Row {summary['total']} Error: {str(e)}")
            
            frappe.flags.in_import = False
            # Force persistence: commit and flush (some setups defer commit until request end)
            frappe.db.commit()
            try:
                frappe.db.sql("COMMIT")
            except Exception:
                pass
    
    except Exception as e:
        frappe.log_error(str(e), "Import Error")
        frappe.db.rollback()
        return f"Ошибка файла: {str(e)}"

    msg = f"Импорт завершен ({import_type}). Всего: {summary['total']}, Успешно: {summary['success']}, Пропущено: {summary['skipped']}, Дубликаты: {summary['duplicates']}, Ошибки: {summary['errors']}."
    if summary["logs"]:
        msg += f"\nОшибки: {'; '.join(summary['logs'][:10])}"
    if import_type == "Импорт платежей" and summary["success"] == 0 and summary["errors"] > 0:
        msg += "\n\nПодсказка: для Импорт платежей сначала выполните Импорт договоров (installment_contracts.csv), чтобы в системе были Sales Order с номерами документов."
    
    # Reset import flag
    frappe.flags.in_import = False
    
    return msg


def process_customer_row(row, summary, skip_validation=False):
    # Customer Import Logic
    
    # 1. Name Parsing
    full_name = row.get("Название клиента", "").strip()
    if not full_name:
        raise SkipRow  # Skip empty names
    
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
        raise SkipRow  # Skip empty rows

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
    
    # Get warehouse for the branch
    warehouse = frappe.db.get_value(
        "Warehouse",
        {"branch": default_branch, "is_default": 1},
        "name"
    )
    if not warehouse:
        # Fallback to any warehouse for this branch
        warehouse = frappe.db.get_value("Warehouse", {"branch": default_branch}, "name")
    if not warehouse:
        # Last resort: get any warehouse
        warehouse = frappe.db.get_value("Warehouse", {}, "name")
    
    so = frappe.new_doc("Sales Order")
    so.customer = customer.name
    so.order_date = sale_date  # Correct field name
    so.delivery_date = sale_date
    so.branch = default_branch
    so.warehouse = warehouse
    so.po_no = doc_number  # Legacy document number
    so.notes = f"Imported from legacy system. Original ID: {doc_number}"
    
    # Set sale type based on payment status
    if remaining_debt > 0:
        so.sale_type = "Рассрочка" if paid_amount == 0 else "Смешанный"
    else:
        so.sale_type = "Наличные"
    
    # Set payment amounts
    so.subtotal = total_amount
    so.total_amount = total_amount
    so.paid_amount = paid_amount
    so.balance_amount = remaining_debt
    
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
    installment_plan = None
    if remaining_debt > 0:
        installment_plan = create_installment_plan(so, customer, row, sale_date, total_amount, paid_amount, remaining_debt)
        
        # Link installment plan back to sales order
        if installment_plan:
            so.installment_plan = installment_plan.name
            so.db_update()
    
    # 5. Create Contract document
    try:
        contract = frappe.new_doc("Contract")
        contract.contract_type = "Рассрочка (BNPL)"
        contract.customer = customer.name
        contract.sales_order = so.name
        contract.installment_plan = installment_plan.name if installment_plan else None
        contract.total_amount = total_amount
        contract.contract_date = sale_date
        contract.status = "Активный" if remaining_debt > 0 else "Завершен"
        
        # Skip all validation and mandatory checks to avoid template lookup errors
        contract.flags.ignore_permissions = True
        contract.flags.ignore_validate = True
        contract.flags.ignore_mandatory = True
        contract.flags.ignore_links = True
        
        # Insert without running validate() method
        contract.insert(ignore_permissions=True, ignore_mandatory=True)
    except Exception as e:
        # Log error but don't fail the entire import
        error_msg = str(e)
        frappe.log_error(f"Contract creation failed for {doc_number}: {error_msg}", "Contract Import Error")
        # Continue without creating contract - Sales Order and Plan were created successfully



def get_or_create_customer(name, phone, skip_validation=False):
    # BNPL Logic helper; phone may be comma/semicolon-separated
    phones = parse_phone_list(phone) if phone else []
    
    # If no valid phone numbers, add a placeholder to satisfy validation
    if not phones:
        phones = ["000000000"]  # Placeholder phone for customers without phone data
    
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
    if code:
        # Get product name first, then fetch the doc
        product_name = frappe.db.get_value("Product", {"product_code": code}, "name")
        if product_name:
            return frappe.get_doc("Product", product_name)
          
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
    # Don't submit during import - legacy data doesn't need full workflow
    # plan.submit()
    
    return plan


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


def parse_payment_details(payment_str):
    """
    Parse payment details string like:
    '05.02.2026=350.000000 USD; 31.01.2026=500.000000 USD'
    Returns list of dicts with 'date' and 'amount'
    """
    payments = []
    if not payment_str or not str(payment_str).strip():
        return payments
    
    # Split by semicolon
    parts = str(payment_str).split(';')
    for part in parts:
        part = part.strip()
        if not part or '=' not in part:
            continue
        
        try:
            # Split by '='
            date_part, amount_part = part.split('=', 1)
            date_part = date_part.strip()
            amount_part = amount_part.strip()
            
            # Parse date (DD.MM.YYYY format)
            payment_date = parse_date(date_part)
            
            # Parse amount (remove currency suffix like USD, UZS)
            amount_str = amount_part.split()[0] if ' ' in amount_part else amount_part
            amount = parse_number(amount_str)
            
            if amount > 0:
                payments.append({
                    'date': payment_date,
                    'amount': amount
                })
        except Exception as e:
            # Skip malformed payment entries
            continue
    
    return payments


def process_payment_row(row, summary, skip_validation=False):
    """
    Process payment import row from installment_payments.csv
    Creates Payment Transaction records for each payment in the history
    """
    doc_number = row.get("Номер документа", "").strip()
    if not doc_number:
        raise SkipRow
    
    # Find the Sales Order by document number
    so_name = frappe.db.get_value("Sales Order", {"po_no": doc_number}, "name")
    if not so_name:
        summary["errors"] += 1
        summary["logs"].append(f"Row {summary.get('current_row', '?')} Error: Sales Order not found for doc number {doc_number}")
        raise SkipRow
    
    # Get the Sales Order
    so = frappe.get_doc("Sales Order", so_name)
    
    # Parse payment details
    payment_details_str = row.get("Детали всех платежей", "")
    payments = parse_payment_details(payment_details_str)
    
    if not payments:
        # No payments to import
        raise SkipRow
    
    # Create Payment Transaction for each payment
    payments_created = 0
    for payment in payments:
        try:
            # Check if payment already exists for this date/amount/sales order
            existing = frappe.db.exists("Payment Transaction", {
                "reference_doctype": "Sales Order",
                "reference_name": so_name,
                "payment_date": payment['date'],
                "amount": payment['amount']
            })
            
            if existing:
                # Skip duplicate
                continue
            
            # Create new payment transaction
            payment_doc = frappe.new_doc("Payment Transaction")
            payment_doc.customer = so.customer
            payment_doc.payment_date = payment['date']
            payment_doc.amount = payment['amount']
            payment_doc.status = "Завершен"  # Completed
            payment_doc.payment_method = "Наличные"  # Default to cash
            payment_doc.reference_doctype = "Sales Order"
            payment_doc.reference_name = so_name
            payment_doc.notes = f"Imported from legacy system. Doc #{doc_number}"
            payment_doc.received_by = frappe.session.user
            
            payment_doc.flags.ignore_permissions = True
            if skip_validation:
                payment_doc.flags.ignore_validate = True
                payment_doc.flags.ignore_mandatory = True
            
            payment_doc.insert()
            payments_created += 1
            
        except Exception as e:
            # Log error but continue with other payments
            summary["logs"].append(f"Doc {doc_number}, Payment {payment['date']}: {str(e)[:100]}")
            continue
    
    return payments_created


def process_supplier_row(row, summary, skip_validation=False):
    name = row.get("Название клиента", "").strip() or row.get("Name", "").strip()
    if not name:
        raise SkipRow

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
        raise SkipRow

    # Find linked Sales Order by po_no (Номер документа) — must run Импорт договоров first
    so_name = frappe.db.get_value("Sales Order", {"po_no": doc_num}, "name")
    if not so_name:
        so_name = frappe.db.get_value("Sales Order", {"notes": ["like", f"%Legacy ID: {doc_num}%"]}, "name")
    if not so_name:
        raise Exception(f"Sales Order не найден для «{doc_num}». Сначала выполните Импорт договоров (installment_contracts.csv).")

    # Create Payment Transaction
    pe = frappe.new_doc("Payment Transaction")
    # Prefer last payment date from CSV, else sale date
    last_payment_raw = row.get("Последний платеж", "").strip()
    if last_payment_raw and "=" in last_payment_raw:
        pe.payment_date = parse_date(last_payment_raw.split("=")[0].strip())
    else:
        pe.payment_date = parse_date(row.get("Дата продажи", ""))
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
        raise SkipRow

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
