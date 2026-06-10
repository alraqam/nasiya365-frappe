
import frappe
from frappe.utils import getdate, flt, cint, now, cstr, today
import csv
import os
import re


class SkipRow(Exception):
    """Raised when a row should be skipped (empty/invalid) without counting as success or error."""
    pass


def normalize_row(row):
    """Strip BOM/whitespace from keys and string values so CSV exports from other systems match."""
    out = {}
    for k, v in (row or {}).items():
        if k is None:
            continue
        key = str(k).strip().strip("\ufeff")
        if isinstance(v, str):
            out[key] = v.strip()
        elif v is None:
            out[key] = ""
        else:
            out[key] = v
    return out


def row_get(row, *keys, default=""):
    for k in keys:
        if k not in row:
            continue
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return default


def customer_display_name(customer):
    """Denormalized list-view label: full_name, else ФИО from parts, else link id."""
    if not customer:
        return ""
    if hasattr(customer, "name"):
        fn = (getattr(customer, "full_name", None) or "").strip()
        if fn:
            return fn
        parts = [
            (getattr(customer, "first_name", None) or "").strip(),
            (getattr(customer, "middle_name", None) or "").strip(),
            (getattr(customer, "last_name", None) or "").strip(),
        ]
        composed = " ".join(p for p in parts if p).strip()
        return composed or customer.name
    row = frappe.db.get_value(
        "Customer Profile",
        customer,
        ("full_name", "first_name", "middle_name", "last_name"),
        as_dict=True,
    )
    if not row:
        return customer
    fn = (row.get("full_name") or "").strip()
    if fn:
        return fn
    parts = [
        (row.get("first_name") or "").strip(),
        (row.get("middle_name") or "").strip(),
        (row.get("last_name") or "").strip(),
    ]
    composed = " ".join(p for p in parts if p).strip()
    return composed or customer


def sibling_installment_payments_csv(contract_csv_path):
    """Same directory as the contracts CSV; standard name from legacy export bundle."""
    if not contract_csv_path:
        return ""
    directory = os.path.dirname(os.path.abspath(contract_csv_path))
    return os.path.join(directory, "installment_payments.csv")


def load_installment_counts_from_payments_csv(payments_csv_path):
    """
    Build map: Номер документа -> Количество платежей (int).
    Used when installment_contracts.csv has no Количество платежей column.
    """
    out = {}
    if not payments_csv_path or not os.path.isfile(payments_csv_path):
        return out
    encoding = "utf-8-sig"
    try:
        with open(payments_csv_path, mode="r", encoding=encoding) as f:
            sample = f.read(1024)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except Exception:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            for raw in reader:
                row = normalize_row(raw)
                doc = row_get(row, "Номер документа", "Номер")
                if not doc:
                    continue
                cnt_raw = row_get(row, "Количество платежей")
                if not cnt_raw:
                    continue
                n = cint(cnt_raw)
                if n > 0:
                    key = doc.strip()
                    out[key] = max(out.get(key, 0), n)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "installment_payments CSV sidecar load")
    return out


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
        "logs": [],
        "installment_counts": {},
        "_installment_counts_source": None,
    }

    payments_sidecar = sibling_installment_payments_csv(file_path)
    if import_type in ("Импорт договоров", "BNPL Sales"):
        summary["installment_counts"] = load_installment_counts_from_payments_csv(payments_sidecar)
        if summary["installment_counts"]:
            summary["_installment_counts_source"] = payments_sidecar

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
                    row = normalize_row(row)
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
    src = summary.get("_installment_counts_source")
    if src and summary.get("installment_counts"):
        msg += (
            f"\nКоличество платежей подставлено из «{os.path.basename(src)}» "
            f"для {len(summary['installment_counts'])} номеров документов (если в договоре колонка пуста)."
        )
    if summary["logs"]:
        msg += f"\nОшибки: {'; '.join(summary['logs'][:10])}"
    if import_type == "Импорт платежей" and summary["success"] == 0 and summary["errors"] > 0:
        msg += "\n\nПодсказка: для Импорт платежей сначала выполните Импорт договоров (installment_contracts.csv), чтобы в системе были Sales Order с номерами документов."
    
    # Reset import flag
    frappe.flags.in_import = False
    
    return msg


def process_customer_row(row, summary, skip_validation=False):
    full_name = row_get(row, "Название клиента", "Name", "Клиент", "ФИО")
    if not full_name:
        raise SkipRow

    parts = full_name.split()
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""
    middle_name = " ".join(parts[2:]) if len(parts) > 2 else ""

    phones = []
    for col in ("Телефон 1", "Телефон 2", "Телефон", "Phone", "Тел"):
        for p in parse_phone_list(row_get(row, col)):
            if p not in phones:
                phones.append(p)

    existing_customer = None
    for p in phones:
        existing = frappe.db.get_value("Customer Phone Number", {"phone_number": p}, "parent")
        if existing:
            existing_customer = frappe.get_doc("Customer Profile", existing)
            summary["duplicates"] += 1
            break

    if existing_customer:
        customer = existing_customer
        customer.first_name = first_name
        customer.last_name = last_name
        customer.middle_name = middle_name or customer.middle_name
    else:
        customer = frappe.new_doc("Customer Profile")
        customer.first_name = first_name
        customer.last_name = last_name
        customer.middle_name = middle_name
        customer.status = "Активный"

    passport_raw = row_get(row, "Серия паспорта", "Паспорт", "Passport")
    if passport_raw:
        match = re.match(r"([A-Z]{2})\s*(\d+)", passport_raw.upper())
        if match:
            customer.passport_series = match.group(1)
            customer.passport_number = match.group(2)
        else:
            customer.passport_number = passport_raw

    dob = row_get(row, "Дата рожд.", "Дата рождения", "Date of Birth")
    if dob:
        d = parse_date(dob)
        if d:
            customer.date_of_birth = d

    address = row_get(row, "Адрес", "Address")
    if address:
        customer.current_address = address

    workplace = row_get(row, "Иш жойи", "Место работы", "Workplace")
    if workplace:
        customer.workplace = workplace

    g_raw = row_get(row, "Пол", "Gender")
    if g_raw:
        gl = g_raw.strip().lower()
        if gl in ("m", "male", "м", "муж", "мужской") or g_raw == "Мужской":
            customer.gender = "Мужской"
        elif gl in ("f", "female", "ж", "жен", "женский") or g_raw == "Женский":
            customer.gender = "Женский"

    pinfl = row_get(row, "ПИНФЛ", "PINFL", "pinfl")
    if pinfl:
        customer.pinfl = re.sub(r"[\s\-]", "", pinfl)

    inc = row_get(row, "Месячный доход", "Monthly income", "monthly_income")
    if inc:
        customer.monthly_income = parse_number(inc)

    if not existing_customer:
        if not phones:
            phones = ["000000000"]
        for i, p in enumerate(phones):
            customer.append(
                "phone_numbers",
                {"phone_number": p, "is_primary": 1 if i == 0 else 0},
            )
    else:
        existing_nums = {ch.phone_number for ch in customer.phone_numbers}
        for p in phones:
            if p and p not in existing_nums:
                customer.append(
                    "phone_numbers",
                    {"phone_number": p, "is_primary": 0},
                )

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

    imei = row.get("IMEI", "").strip()
    sale_date = parse_date(row.get("Дата продажи", ""))
    if not sale_date:
        raise SkipRow

    # Composite uniqueness check: IMEI + sale date
    # Same IMEI can be resold at a different date (trade-in scenario)
    if imei and sale_date:
        existing_so = frappe.db.sql("""
            SELECT so.name FROM `tabSales Order` so
            INNER JOIN `tabSales Order Item` soi ON soi.parent = so.name
            WHERE soi.imei = %s AND so.order_date = %s AND so.docstatus = 1
            LIMIT 1
        """, (imei, sale_date), as_dict=True)
        if existing_so:
            summary["duplicates"] += 1
            raise Exception(f"Дубликат: IMEI {imei} уже существует за дату {sale_date}")

    # 1. Create/Get Customer (phone may be comma-separated)
    client_name = row.get("Клиент", "").strip()
    phone_raw = row.get("Телефон", "").strip()
    customer = get_or_create_customer(client_name, phone_raw, skip_validation)

    # 2. Create/Get Product
    product_name = row.get("Наименование товара", "").strip()
    product_code = row.get("Код товара", "").strip()
    price = parse_number(row.get("Цена продажи", "0"))
    product = get_or_create_product(product_name, product_code, imei, price, skip_validation)

    # 3. Create Sales Order
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
    so.customer_name = customer_display_name(customer)
    so.order_date = sale_date  # Correct field name
    so.delivery_date = sale_date
    so.branch = default_branch
    so.warehouse = warehouse
    so.po_no = doc_number  # Legacy document number
    so.notes = f"Imported from legacy system. Original ID: {doc_number}"
    
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
        "amount": price,
        "imei": imei
    })
    
    so.flags.ignore_permissions = True
    if skip_validation:
        so.flags.ignore_validate = True
        so.flags.ignore_mandatory = True
        
    so.insert()
    so.submit()

    # Link contract with purchase import via IMEI
    if imei:
        purchase_entry = frappe.db.sql("""
            SELECT se.name, sei.rate as cost_price
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Item` sei ON sei.parent = se.name
            WHERE sei.imei = %s
            AND se.entry_type = 'Поступление'
            AND se.docstatus = 1
            ORDER BY se.posting_date DESC
            LIMIT 1
        """, (imei,), as_dict=True)
        if purchase_entry:
            so.notes = (so.notes or "") + f"\nLinked Purchase: {purchase_entry[0].name}"
            so.db_update()

    # 4. Create Installment Plan if there is debt
    installment_plan = None
    if remaining_debt > 0:
        installment_plan = create_installment_plan(
            so, customer, row, sale_date, total_amount, paid_amount, remaining_debt, summary
        )

    # 5. b3-merge: move Contract data into Installment Plan
    # In legacy flow we created a separate `Contract`. Now we populate contract-related
    # fields directly on the created `Installment Plan` (when BNPL/debt exists).
    if installment_plan:
        installment_plan.contract_type = "Рассрочка (BNPL)"
        installment_plan.contract_number = installment_plan.contract_number or installment_plan.name
        installment_plan.contract_date = sale_date
        installment_plan.valid_until = installment_plan.end_date or sale_date
        installment_plan.contract_status = "Подписан"

        installment_plan.product_name = product_name
        installment_plan.imei = imei

        debt_today = 0
        for s in installment_plan.schedule or []:
            if not s.due_date:
                continue
            if getdate(s.due_date) <= getdate(today()) and s.status in ["Ожидает", "Просрочен", "Частично"]:
                debt_today += flt(s.amount) - flt(s.paid_amount)
        installment_plan.debt_today = debt_today

        installment_plan.flags.ignore_permissions = True
        installment_plan.flags.ignore_validate = True
        installment_plan.flags.ignore_mandatory = True
        installment_plan.db_update()



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
    customer.status = "Активный"
    
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
    
    item.flags.ignore_permissions = True
    if skip_validation:
        item.flags.ignore_validate = True
        item.flags.ignore_mandatory = True
        
    item.insert()
    return item

def _map_installment_frequency(raw):
    if not raw:
        return "Ежемесячно"
    r = str(raw).strip().lower()
    allowed = ("Ежемесячно", "Еженедельно", "Раз в две недели")
    if raw in allowed:
        return raw
    mapping = {
        "monthly": "Ежемесячно",
        "ежемесячно": "Ежемесячно",
        "weekly": "Еженедельно",
        "еженедельно": "Еженедельно",
        "biweekly": "Раз в две недели",
        "bi-weekly": "Раз в две недели",
        "раз в две недели": "Раз в две недели",
    }
    return mapping.get(r, "Ежемесячно")


def create_installment_plan(
    so, customer, row, sale_date, total_amount, paid_amount, remaining_debt, summary=None
):
    payment_count_raw = row_get(row, "Количество платежей", "Кол-во платежей")
    payment_count = cint(payment_count_raw) if payment_count_raw else 0

    doc_number = row_get(row, "Номер документа", "Номер")
    if payment_count <= 0 and summary and summary.get("installment_counts") and doc_number:
        mapped = summary["installment_counts"].get(doc_number.strip())
        if mapped:
            payment_count = cint(mapped)

    num_installments = payment_count - 1 if payment_count > 0 else 0
    if num_installments < 1:
        num_installments = 1

    plan = frappe.new_doc("Installment Plan")
    plan.customer = customer.name
    plan.customer_name = customer_display_name(customer)
    plan.sales_order = so.name
    plan.start_date = sale_date

    plan.principal_amount = total_amount
    plan.down_payment = paid_amount
    plan.financed_amount = remaining_debt
    plan.number_of_installments = num_installments
    plan.installment_amount = remaining_debt / num_installments if num_installments > 0 else remaining_debt
    freq_raw = row_get(row, "Частота платежей", "frequency")
    plan.frequency = _map_installment_frequency(freq_raw)

    plan.flags.ignore_permissions = True
    plan.insert()
    # validate() returns early during import, so schedule is not generated on insert
    plan.reload()
    plan.flags.ignore_permissions = True
    plan.calculate_amounts()
    plan.generate_schedule()
    plan.save(ignore_permissions=True)
    # Submit so docstatus=1 and on_submit hook sets status="Активный", updates customer
    # statistics, and creates the contract. is_submittable=1 makes this the canonical path.
    plan.reload()
    plan.flags.ignore_permissions = True
    plan.submit()

    return plan


def parse_number(val):
    """Parse numeric CSV values.

    The legacy CSV uses CIS/European decimal-comma format:
        950,000   → 950.000 → 950  (comma = decimal separator, trailing zeros)
        1 080,000 → 1080.000 → 1080
        1,00      → 1.00 → 1
    Only pattern like 1,234,567 (multiple commas with 3-digit groups) is a
    genuine thousands separator.
    """
    if val is None:
        return 0.0
    s = str(val).strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return 0.0
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if not s:
        return 0.0
    if "," in s:
        parts = s.split(",")
        if len(parts) == 2:
            # Single comma → always decimal separator (CIS format)
            int_part = parts[0].replace(".", "")
            s = int_part + "." + parts[1]
        else:
            # Multiple commas → thousands separators (e.g. 1,234,567)
            s = s.replace(",", "")
    try:
        n = flt(s)
        return -n if neg else n
    except Exception:
        return 0.0


def parse_date(val):
    """
    Parse common legacy date formats. Returns YYYY-MM-DD string or None (never silently use 'now').
    """
    if val is None or str(val).strip() == "":
        return None
    val = str(val).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", val):
        return val[:10]
    for sep in (".", "/"):
        parts = val.split(sep) if sep in val else None
        if parts and len(parts) == 3:
            try:
                day, month, year = parts[0], parts[1], parts[2]
                if len(year) == 2:
                    year = "20" + year
                if len(day) == 1:
                    day = "0" + day
                if len(month) == 1:
                    month = "0" + month
                if len(year) == 4 and day.isdigit() and month.isdigit():
                    return f"{year}-{month}-{day}"
            except Exception:
                pass
    try:
        return str(getdate(val))[:10]
    except Exception:
        return None

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
            
            if amount > 0 and payment_date:
                payments.append({
                    'date': payment_date,
                    'amount': amount
                })
        except Exception as e:
            # Skip malformed payment entries
            continue
    
    return payments


def _payment_transaction_exists(so_name, payment_date, amount):
    row = frappe.db.sql(
        """
        SELECT name FROM `tabPayment Transaction`
        WHERE reference_doctype = 'Sales Order' AND reference_name = %s
          AND payment_date = %s AND ABS(amount - %s) < 0.01
        LIMIT 1
        """,
        (so_name, payment_date, amount),
    )
    return bool(row)


def _resolve_sales_order_for_payment_import(doc_number):
    so_name = frappe.db.get_value("Sales Order", {"po_no": doc_number}, "name")
    if so_name:
        return so_name
    for pattern in (
        f"%Original ID: {doc_number}%",
        f"%Legacy ID: {doc_number}%",
    ):
        so_name = frappe.db.get_value("Sales Order", {"notes": ["like", pattern]}, "name")
        if so_name:
            return so_name
    return None


def _insert_payment_lines(so_name, doc_number, payments, summary, skip_validation):
    payments_created = 0
    customer = frappe.db.get_value("Sales Order", so_name, "customer")
    for payment in payments:
        pdate = payment.get("date")
        amount = flt(payment.get("amount"))
        if not pdate or amount <= 0:
            continue
        try:
            if _payment_transaction_exists(so_name, pdate, amount):
                continue
            payment_doc = frappe.new_doc("Payment Transaction")
            payment_doc.customer = customer
            payment_doc.customer_name = customer_display_name(customer)
            payment_doc.payment_date = pdate
            payment_doc.amount = amount
            payment_doc.status = "Завершен"
            payment_doc.payment_method = "Наличные"
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
            summary["logs"].append(f"Doc {doc_number}, Payment {pdate}: {str(e)[:100]}")
    return payments_created


def process_payment_row(row, summary, skip_validation=False):
    """Детали всех платежей (множественные) или агрегат Всего оплачено / Оплачено."""
    doc_number = row_get(row, "Номер документа", "Номер")
    if not doc_number:
        raise SkipRow

    so_name = _resolve_sales_order_for_payment_import(doc_number)
    if not so_name:
        raise Exception(
            f"Sales Order не найден для «{doc_number}». Сначала выполните импорт договоров/продаж."
        )

    detail_raw = row_get(row, "Детали всех платежей", "Детали платежей")
    payments = parse_payment_details(detail_raw) if detail_raw else []

    if payments:
        created = _insert_payment_lines(so_name, doc_number, payments, summary, skip_validation)
        if created == 0:
            raise SkipRow
        return

    amount = parse_number(row_get(row, "Всего оплачено", "Total paid"))
    if amount <= 0:
        amount = parse_number(row_get(row, "Оплачено", "Paid"))
    if amount <= 0:
        raise SkipRow

    last_payment_raw = row_get(row, "Последний платеж")
    pay_date = None
    if last_payment_raw and "=" in last_payment_raw:
        pay_date = parse_date(last_payment_raw.split("=", 1)[0].strip())
    if not pay_date:
        pay_date = parse_date(row_get(row, "Дата продажи"))
    if not pay_date:
        raise SkipRow

    if _payment_transaction_exists(so_name, pay_date, amount):
        raise SkipRow

    pe = frappe.new_doc("Payment Transaction")
    pe.customer = frappe.db.get_value("Sales Order", so_name, "customer")
    pe.customer_name = customer_display_name(pe.customer)
    pe.payment_date = pay_date
    pe.amount = amount
    pe.status = "Завершен"
    pe.payment_method = "Наличные"
    pe.reference_doctype = "Sales Order"
    pe.reference_name = so_name
    pe.notes = f"Imported from legacy system. Doc #{doc_number}"
    pe.received_by = frappe.session.user
    pe.flags.ignore_permissions = True
    if skip_validation:
        pe.flags.ignore_validate = True
        pe.flags.ignore_mandatory = True
    pe.insert()


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
    """Purchase Import → Stock Entry (Поступление). Also backfills Product attrs."""
    supplier_name = row.get("Поставщик", "").strip()

    if supplier_name and not frappe.db.exists("Supplier", {"supplier_name": supplier_name}):
        s = frappe.new_doc("Supplier")
        s.supplier_name = supplier_name
        s.flags.ignore_permissions = True
        s.insert()

    pi_date = parse_date(row.get("Дата покупки", ""))
    if not pi_date:
        pi_date = getdate(today())

    item_code = row.get("Код товара", "").strip()
    item_name = row.get("Махсулот", "").strip()
    rate = parse_number(row.get("Таннарх", "0"))
    imei = row.get("Имейка", "").strip()

    product = get_or_create_product(item_name, item_code, imei, rate, skip_validation)

    # Backfill color/storage/brand from purchase CSV
    color = row.get("Цвет", "").strip()
    storage = row.get("Память", "").strip()
    brand = row.get("Бренд", "").strip()
    changed = False
    if color and not (product.color or "").strip():
        product.color = color
        changed = True
    if storage and not (product.storage or "").strip():
        product.storage = storage
        changed = True
    if brand and not (product.brand or "").strip():
        product.brand = brand
        changed = True
    if changed:
        product.flags.ignore_permissions = True
        product.save(ignore_permissions=True)

    se = frappe.new_doc("Stock Entry")
    se.entry_type = "Поступление"
    se.posting_date = pi_date
    se.posting_time = now()

    se.warehouse = frappe.db.get_value("Warehouse", {"branch": default_branch}, "name")
    if not se.warehouse:
        se.warehouse = frappe.db.get_value("Warehouse", {}, "name")

    if supplier_name:
        se.supplier = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")

    se.remarks = f"Imported Purchase from Supplier: {supplier_name}"

    item_data = {
        "product": product.name,
        "product_name": product.product_name,
        "quantity": 1,
        "rate": rate,
        "amount": rate,
        "imei": imei,
    }
    if color:
        item_data["color"] = color
    if storage:
        item_data["storage"] = storage
    se.append("items", item_data)

    se.flags.ignore_permissions = True
    if skip_validation:
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True

    se.insert()
    se.submit()

def process_stock_entry_csv(row, default_branch, summary, skip_validation=False):
    """Stock Entry Import → entry_type = 'Корректировка' (inventory snapshot)."""
    code = row.get("Код товара", "").strip()
    name = row.get("Наименование товара", "").strip()
    imei = row.get("Серийный номер", "").strip()

    if not code and not name:
        raise SkipRow

    condition = row.get("Состояние", "").strip()
    color = row.get("Цвет", "").strip()
    brand = row.get("Бренд", "").strip()

    # Skip if an inventory-adjustment entry already exists for this serial
    if imei:
        dup = frappe.db.sql("""
            SELECT sei.parent FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Item` sei ON sei.parent = se.name
            WHERE se.entry_type = 'Корректировка' AND sei.imei = %s
            AND se.docstatus = 1 LIMIT 1
        """, (imei,))
        if dup:
            summary["duplicates"] += 1
            return

    # Get or create Product; backfill color/storage/brand
    product = None
    if code:
        pname = frappe.db.get_value("Product", {"product_code": code}, "name")
        if pname:
            product = frappe.get_doc("Product", pname)
            changed = False
            if color and not (product.color or "").strip():
                product.color = color
                changed = True
            if brand and not (product.brand or "").strip():
                product.brand = brand
                changed = True
            if changed:
                product.flags.ignore_permissions = True
                product.save(ignore_permissions=True)

    if not product:
        product = frappe.new_doc("Product")
        product.product_name = name or code
        product.product_code = code
        product.selling_price = 0
        if brand:
            product.brand = brand
        if color:
            product.color = color

        desc_parts = []
        if brand:
            desc_parts.append(f"Бренд: {brand}")
        if color:
            desc_parts.append(f"Цвет: {color}")
        if condition:
            desc_parts.append(f"Состояние: {condition}")
        if desc_parts:
            product.description = ", ".join(desc_parts)

        product.category = "Smartphones"
        if "MacBook" in name or "iMac" in name:
            product.category = "Laptops" if "MacBook" in name else "Computers"
        if not frappe.db.exists("Product Category", product.category):
            cat = frappe.new_doc("Product Category")
            cat.category_name = product.category
            cat.flags.ignore_permissions = True
            cat.insert()

        product.flags.ignore_permissions = True
        product.insert()

    # Inherit purchase cost for the item row
    rate = 0.0
    if imei:
        pr = frappe.db.sql("""
            SELECT sei.rate FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Item` sei ON sei.parent = se.name
            WHERE se.entry_type = 'Поступление' AND sei.imei = %s
            AND se.docstatus = 1 ORDER BY se.posting_date DESC LIMIT 1
        """, (imei,))
        if pr:
            rate = flt(pr[0][0])

    se = frappe.new_doc("Stock Entry")
    se.entry_type = "Корректировка"
    se.posting_date = now()

    if default_branch:
        warehouse = frappe.db.get_value("Warehouse", {"branch": default_branch}, "name")
        if warehouse:
            se.warehouse = warehouse
    if not se.warehouse:
        se.warehouse = frappe.db.get_value("Warehouse", {}, "name")

    se.remarks = f"Текущий остаток: {name} ({imei})"

    item_data = {
        "product": product.name,
        "quantity": 1,
        "imei": imei,
        "rate": rate,
        "amount": rate,
    }
    if color:
        item_data["color"] = color
    if condition:
        item_data["condition"] = "Новый" if condition.upper() == "NEW" else "Б/У"
    se.append("items", item_data)

    se.flags.ignore_permissions = True
    if skip_validation:
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True

    se.insert()
    se.submit()
