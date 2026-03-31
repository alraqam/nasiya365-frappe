"""
Installation Scripts for Nasiya365
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, today, getdate
import csv
import os
import re


def after_install():
    """Run after app installation"""
    create_default_roles()
    sync_workspace()
    sync_workspace_sidebar()
    set_default_desk_home()
    create_default_print_templates()
    frappe.db.commit()
    print("Nasiya365 installed successfully!")


def after_migrate():
    """Re-sync workspace from app JSON and desk defaults after every migrate."""
    sync_workspace()
    sync_workspace_sidebar()
    set_default_desk_home()
    backfill_customer_profile_full_name()
    sync_customer_name_denormalized()
    backfill_stock_entry_supplier_from_remarks()
    frappe.db.commit()


def backfill_stock_entry_supplier_from_remarks():
    """
    Older imports set supplier only in remarks. Link supplier when remarks match
    'Imported Purchase from Supplier: <name>' and a Supplier with that supplier_name exists.
    """
    if not frappe.db.exists("DocType", "Stock Entry"):
        return
    if not frappe.db.has_column("Stock Entry", "supplier"):
        return
    prefix = "Imported Purchase from Supplier: "
    rows = frappe.db.sql(
        """
        SELECT name, remarks FROM `tabStock Entry`
        WHERE entry_type = %s
        AND IFNULL(supplier, '') = ''
        AND IFNULL(remarks, '') LIKE %s
        """,
        ("Поступление", prefix + "%"),
        as_dict=True,
    )
    for r in rows or []:
        raw = (r.get("remarks") or "")[len(prefix) :].strip()
        if not raw:
            continue
        supplier_name = raw.split("\n", 1)[0].strip()
        if not supplier_name:
            continue
        sid = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")
        if sid:
            frappe.db.set_value("Stock Entry", r["name"], "supplier", sid, update_modified=False)


@frappe.whitelist()
def purge_contract_records():
    """Delete all legacy Contract records (b3 merge keeps data on Installment Plan)."""
    if not frappe.db.exists("DocType", "Contract"):
        return 0
    names = frappe.get_all("Contract", pluck="name")
    if not names:
        return 0
    # Fast bulk delete. Contract has no child tables.
    frappe.db.sql("DELETE FROM `tabContract`")
    frappe.db.commit()
    return len(names)


def backfill_customer_profile_full_name():
    """Set full_name from name parts when empty (imported rows often skipped validate)."""
    if not frappe.db.exists("DocType", "Customer Profile"):
        return
    if not frappe.db.has_column("Customer Profile", "full_name"):
        return
    frappe.db.sql(
        """
        UPDATE `tabCustomer Profile`
        SET full_name = TRIM(CONCAT_WS(' ',
            NULLIF(TRIM(IFNULL(first_name, '')), ''),
            NULLIF(TRIM(IFNULL(middle_name, '')), ''),
            NULLIF(TRIM(IFNULL(last_name, '')), '')
        ))
        WHERE (full_name IS NULL OR TRIM(IFNULL(full_name, '')) = '')
        AND (
            TRIM(IFNULL(first_name, '')) != ''
            OR TRIM(IFNULL(middle_name, '')) != ''
            OR TRIM(IFNULL(last_name, '')) != ''
        )
        """
    )


def sync_customer_name_denormalized():
    """
    Keep list-view / title_field column customer_name in sync with the client profile.
    Uses full_name, then first/middle/last, then falls back to CUST-####.
    """
    if not frappe.db.exists("DocType", "Customer Profile"):
        return
    for dt in ("Sales Order", "Installment Plan", "Payment Transaction", "Contract"):
        if not frappe.db.exists("DocType", dt):
            continue
        if not frappe.db.has_column(dt, "customer_name"):
            continue
        # Physical table name: "tab" + DocType name (e.g. tabSales Order)
        table = "tab" + dt
        frappe.db.sql(
            f"""
            UPDATE `{table}` AS t
            INNER JOIN `tabCustomer Profile` AS c ON c.name = t.customer
            SET t.customer_name = COALESCE(
                NULLIF(TRIM(IFNULL(c.full_name, '')), ''),
                NULLIF(TRIM(CONCAT_WS(' ',
                    NULLIF(TRIM(IFNULL(c.first_name, '')), ''),
                    NULLIF(TRIM(IFNULL(c.middle_name, '')), ''),
                    NULLIF(TRIM(IFNULL(c.last_name, '')), '')
                )), ''),
                t.customer
            )
            WHERE IFNULL(t.customer, '') != ''
            """
        )


def set_default_desk_home():
    """Land on Nasiya365 workspace so sidebar uses Workspace links (not the flat module DocType list)."""
    if frappe.db.exists("Workspace", "Nasiya365"):
        frappe.db.set_default("desktop:home_page", "nasiya365")
    elif frappe.db.exists("Page", "bnpl-control-center"):
        frappe.db.set_default("desktop:home_page", "bnpl-control-center")


def sync_workspace():
    """Ensure Nasiya365 workspace is synced from app JSON so it shows on the desk."""
    for name in ("Nasiya365", "nasiya365"):
        try:
            frappe.reload_doc("nasiya365", "workspace", name, force=True)
            print("Nasiya365 workspace synced.")
            return
        except Exception as e:
            continue
    frappe.log_error("Workspace sync failed: nasiya365 workspace not found", "Nasiya365 Install")


def sync_workspace_sidebar():
    """
    Frappe v16+ builds a flat DocType list per module via auto_generate_sidebar_from_module()
    unless a Workspace Sidebar row exists for that module (see frappe.boot.get_sidebar_items).

    Creating this document replaces the flat menu with the same Russian structure as the Workspace.
    """
    if not frappe.db.exists("DocType", "Workspace Sidebar"):
        return

    mod_rows = frappe.get_all("Module Def", filters={"app_name": "nasiya365"}, pluck="name", limit=1)
    module_name = mod_rows[0] if mod_rows else "nasiya365"

    rows = _nasiya365_workspace_sidebar_items()

    existing = frappe.db.sql(
        """
        SELECT name FROM `tabWorkspace Sidebar`
        WHERE module = %s AND IFNULL(for_user, '') = ''
        LIMIT 1
        """,
        (module_name,),
    )

    if existing:
        doc = frappe.get_doc("Workspace Sidebar", existing[0][0])
    else:
        doc = frappe.new_doc("Workspace Sidebar")
        doc.title = module_name
        doc.module = module_name

    doc.header_icon = "money"
    doc.app = "nasiya365"
    doc.for_user = None
    doc.items = []
    for row in rows:
        doc.append("items", row)

    doc.save(ignore_permissions=True)
    print(f"Nasiya365 Workspace Sidebar synced (module={module_name}).")


def _nasiya365_workspace_sidebar_items():
    """Ordered Workspace Sidebar Item rows (labels in Russian)."""
    idx = 1
    out = []

    def link(label, link_type, link_to, child=0):
        nonlocal idx
        out.append(
            {
                "label": label,
                "type": "Link",
                "link_type": link_type,
                "link_to": link_to,
                "idx": idx,
                "child": child,
            }
        )
        idx += 1

    def section(label):
        nonlocal idx
        out.append(
            {
                "label": label,
                "type": "Section Break",
                "idx": idx,
                "collapsible": 1,
            }
        )
        idx += 1

    link("Дашборд", "Page", "bnpl-control-center", child=0)
    section("💰 Основное")
    link("Продажи", "DocType", "Sales Order", child=1)
    link("Платежи", "DocType", "Payment Transaction", child=1)
    link("Коллекции", "Page", "overdue-collector", child=1)
    section("👥 Клиенты")
    link("Клиенты", "DocType", "Customer Profile", child=1)
    section("📦 Операции")
    link("Товары", "DocType", "Product", child=1)
    link("Склад", "DocType", "Stock Entry", child=1)
    section("⚙️ Финансы")
    link("Рассрочка", "DocType", "Installment Plan", child=1)
    link("Расходы", "DocType", "Expense", child=1)
    link("Касса", "DocType", "Cashbox", child=1)
    link("Настройки", "DocType", "Merchant Settings", child=1)
    return out


def create_default_roles():
    """Create default roles for Nasiya365"""
    roles = [
        {
            "role_name": "Nasiya365 Admin",
            "desk_access": 1,
            "description": "Full access to all Nasiya365 features"
        },
        {
            "role_name": "Branch Manager",
            "desk_access": 1,
            "description": "Manage branch operations and staff"
        },
        {
            "role_name": "Salesperson",
            "desk_access": 1,
            "description": "Create sales and BNPL applications"
        },
        {
            "role_name": "Cashier",
            "desk_access": 1,
            "description": "Process payments and cash receipts"
        },
        {
            "role_name": "Collector",
            "desk_access": 1,
            "description": "Follow up on overdue payments"
        },
        {
            "role_name": "Credit Officer",
            "desk_access": 1,
            "description": "Approve or reject credit applications"
        },
        {
            "role_name": "Warehouse Manager",
            "desk_access": 1,
            "description": "Manage inventory and stock movements"
        },
    ]
    
    for role_data in roles:
        if not frappe.db.exists("Role", role_data["role_name"]):
            role = frappe.new_doc("Role")
            role.role_name = role_data["role_name"]
            role.desk_access = role_data.get("desk_access", 1)
            role.insert(ignore_permissions=True)
            print(f"Created role: {role_data['role_name']}")


def create_default_print_templates():
    """Create default print templates"""
    # Templates will be created when Print Template DocType is ready
    pass


# ---------------------------------------------------------------------------
# DATA REPAIR FUNCTIONS — run via: bench execute nasiya365.install.<name>
# ---------------------------------------------------------------------------

def _import_data_dir():
    """Path to import-data/ relative to the app root."""
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(app_root, "import-data")


def _open_csv(path):
    """Open a CSV with autodetected dialect and UTF-8-sig encoding."""
    with open(path, mode="r", encoding="utf-8-sig") as f:
        sample = f.read(1024)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except Exception:
            dialect = csv.excel
        return list(csv.DictReader(f, dialect=dialect))


def _norm(row):
    out = {}
    for k, v in (row or {}).items():
        if k is None:
            continue
        key = str(k).strip().strip("\ufeff")
        out[key] = str(v).strip() if isinstance(v, str) else ("" if v is None else v)
    return out


def _parse_number(val):
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
            int_part = parts[0].replace(".", "")
            s = int_part + "." + parts[1]
        else:
            s = s.replace(",", "")
    try:
        n = flt(s)
        return -n if neg else n
    except Exception:
        return 0.0


@frappe.whitelist()
def repair_delete_ghost_installment_plans():
    """
    Delete ghost Installment Plans that have financed_amount = 0
    but the linked Sales Order has balance > 0 (= duplicate artifacts).
    """
    ghosts = frappe.db.sql("""
        SELECT ip.name
        FROM `tabInstallment Plan` ip
        INNER JOIN `tabSales Order` so ON so.name = ip.sales_order
        WHERE (ip.financed_amount = 0 OR ip.financed_amount IS NULL)
        AND so.balance_amount > 0
    """, as_dict=True)

    if not ghosts:
        print("No ghost Installment Plans found.")
        return 0

    names = [g["name"] for g in ghosts]
    print(f"Found {len(names)} ghost IPs to delete...")

    # Delete child table rows first
    frappe.db.sql("""
        DELETE FROM `tabInstallment Schedule`
        WHERE parent IN ({})
    """.format(",".join(["%s"] * len(names))), names)

    # Delete the parent IPs
    frappe.db.sql("""
        DELETE FROM `tabInstallment Plan`
        WHERE name IN ({})
    """.format(",".join(["%s"] * len(names))), names)

    # Clear stale installment_plan links on Sales Orders pointing to deleted IPs
    frappe.db.sql("""
        UPDATE `tabSales Order`
        SET installment_plan = NULL
        WHERE installment_plan IN ({})
    """.format(",".join(["%s"] * len(names))), names)

    frappe.db.commit()
    print(f"Deleted {len(names)} ghost Installment Plans.")
    return len(names)


@frappe.whitelist()
def repair_installment_plan_counts():
    """
    Read installment_payments.csv (which has 'Количество платежей'),
    match to Sales Orders by po_no via installment_contracts.csv doc numbers,
    find the surviving Installment Plan (by sales_order field), update
    number_of_installments, recalculate amounts, regenerate schedules,
    and re-link SO → IP if the link was cleared by ghost deletion.
    """
    payments_csv = os.path.join(_import_data_dir(), "installment_payments.csv")
    if not os.path.isfile(payments_csv):
        print(f"CSV not found: {payments_csv}")
        return

    rows = _open_csv(payments_csv)
    fixed = 0
    skipped = 0

    for raw in rows:
        row = _norm(raw)
        doc_number = row.get("Номер документа", "").strip()
        if not doc_number:
            continue

        payment_count = cint(row.get("Количество платежей", "0"))
        if payment_count <= 0:
            skipped += 1
            continue

        num_installments = max(payment_count - 1, 1)

        so_name = frappe.db.get_value("Sales Order", {"po_no": doc_number}, "name")
        if not so_name:
            skipped += 1
            continue

        # Find IP by sales_order field (more reliable than SO.installment_plan
        # which may have been cleared when ghost IPs were deleted)
        ip_name = frappe.db.get_value(
            "Installment Plan",
            {"sales_order": so_name, "financed_amount": [">", 0]},
            "name",
        )
        if not ip_name:
            # Fallback: any IP linked to this SO
            ip_name = frappe.db.get_value(
                "Installment Plan", {"sales_order": so_name}, "name"
            )
        if not ip_name:
            skipped += 1
            continue

        ip = frappe.get_doc("Installment Plan", ip_name)

        already_ok = (
            ip.number_of_installments == num_installments
            and len(ip.schedule or []) == num_installments
        )
        if already_ok:
            # Still ensure SO ↔ IP link is intact
            if frappe.db.get_value("Sales Order", so_name, "installment_plan") != ip_name:
                frappe.db.set_value("Sales Order", so_name, "installment_plan", ip_name, update_modified=False)
            skipped += 1
            continue

        ip.number_of_installments = num_installments
        # Normalize frequency to bilingual Select value
        freq = (ip.frequency or "").strip()
        FREQ_MAP = {
            "Ежемесячно": "Ежемесячно (Monthly)",
            "Еженедельно": "Еженедельно (Weekly)",
            "Раз в две недели": "Раз в две недели (Biweekly)",
        }
        ip.frequency = FREQ_MAP.get(freq, freq) or "Ежемесячно (Monthly)"
        ip.flags.ignore_permissions = True
        ip.flags.ignore_validate = True
        ip.flags.ignore_mandatory = True
        ip.calculate_amounts()
        ip.generate_schedule()
        ip.save(ignore_permissions=True)

        # Re-establish SO → IP link
        frappe.db.set_value("Sales Order", so_name, "installment_plan", ip_name, update_modified=False)
        fixed += 1

    frappe.db.commit()
    print(f"Fixed {fixed} Installment Plans, skipped {skipped}.")
    return fixed


@frappe.whitelist()
def repair_product_color_storage():
    """
    Read purchase.csv and backfill color + storage on Product records.
    Uses the most recent purchase row per product_code as the source.
    """
    csv_path = os.path.join(_import_data_dir(), "purchase.csv")
    if not os.path.isfile(csv_path):
        print(f"CSV not found: {csv_path}")
        return

    rows = _open_csv(csv_path)
    product_data = {}

    for raw in rows:
        row = _norm(raw)
        code = row.get("Код товара", "").strip()
        if not code:
            continue
        color = row.get("Цвет", "").strip()
        storage = row.get("Память", "").strip() or row.get("Ёмкость батареи", "").strip()
        brand = row.get("Бренд", "").strip()
        condition = row.get("Состояние", "").strip()

        if code not in product_data:
            product_data[code] = {}
        if color:
            product_data[code]["color"] = color
        if storage:
            product_data[code]["storage"] = storage
        if brand:
            product_data[code]["brand"] = brand
        if condition:
            product_data[code]["condition"] = condition

    updated = 0
    for code, data in product_data.items():
        product_name = frappe.db.get_value("Product", {"product_code": code}, "name")
        if not product_name:
            continue

        updates = {}
        current = frappe.db.get_value("Product", product_name, ["color", "storage", "brand"], as_dict=True)
        if data.get("color") and not (current.get("color") or "").strip():
            updates["color"] = data["color"]
        if data.get("storage") and not (current.get("storage") or "").strip():
            updates["storage"] = data["storage"]
        if data.get("brand") and not (current.get("brand") or "").strip():
            updates["brand"] = data["brand"]

        if updates:
            for field, val in updates.items():
                frappe.db.set_value("Product", product_name, field, val, update_modified=False)
            updated += 1

    # Also propagate to existing Stock Entry Items and Sales Order Items
    frappe.db.sql("""
        UPDATE `tabStock Entry Item` sei
        INNER JOIN `tabProduct` p ON p.name = sei.product
        SET sei.color = p.color, sei.storage = p.storage
        WHERE (IFNULL(sei.color, '') = '' OR IFNULL(sei.storage, '') = '')
        AND (IFNULL(p.color, '') != '' OR IFNULL(p.storage, '') != '')
    """)

    frappe.db.sql("""
        UPDATE `tabSales Order Item` soi
        INNER JOIN `tabProduct` p ON p.name = soi.product
        SET soi.color = p.color, soi.storage = p.storage
        WHERE (IFNULL(soi.color, '') = '' OR IFNULL(soi.storage, '') = '')
        AND (IFNULL(p.color, '') != '' OR IFNULL(p.storage, '') != '')
    """)

    frappe.db.commit()
    print(f"Updated color/storage on {updated} products, propagated to Stock Entry & Sales Order items.")
    return updated


@frappe.whitelist()
def repair_import_stock_inventory():
    """
    Import stock_entry.csv items as Stock Entry with entry_type = 'Корректировка'
    (inventory adjustment) to mark items currently in stock.
    Skips duplicates by checking serial_no already exists as 'Корректировка'.
    Also reads color/storage/condition from purchase.csv for the same items.
    """
    csv_path = os.path.join(_import_data_dir(), "stock_entry.csv")
    if not os.path.isfile(csv_path):
        print(f"CSV not found: {csv_path}")
        return

    rows = _open_csv(csv_path)
    created = 0
    skipped = 0

    for raw in rows:
        row = _norm(raw)
        code = row.get("Код товара", "").strip()
        name = row.get("Наименование товара", "").strip()
        serial_no = row.get("Серийный номер", "").strip()
        if not code and not name:
            continue

        color = row.get("Цвет", "").strip()
        condition_raw = row.get("Состояние", "").strip()
        brand = row.get("Бренд", "").strip()

        # Check for existing Корректировка entry with same serial_no
        if serial_no:
            exists = frappe.db.sql("""
                SELECT sei.parent FROM `tabStock Entry` se
                INNER JOIN `tabStock Entry Item` sei ON sei.parent = se.name
                WHERE se.entry_type = 'Корректировка' AND sei.serial_no = %s
                AND se.docstatus = 1
                LIMIT 1
            """, (serial_no,))
            if exists:
                skipped += 1
                continue

        product = None
        if code:
            pname = frappe.db.get_value("Product", {"product_code": code}, "name")
            if pname:
                product = frappe.get_doc("Product", pname)

        if not product:
            product = frappe.new_doc("Product")
            product.product_name = name or code
            product.product_code = code or frappe.generate_hash(length=8)
            product.selling_price = 0
            product.product_cost = 0
            if brand:
                product.brand = brand
            if color:
                product.color = color
            product.flags.ignore_permissions = True
            product.insert()

        # Look up cost from the purchase entry for this serial_no
        rate = 0.0
        if serial_no:
            purchase_rate = frappe.db.sql("""
                SELECT sei.rate FROM `tabStock Entry` se
                INNER JOIN `tabStock Entry Item` sei ON sei.parent = se.name
                WHERE se.entry_type = 'Поступление' AND sei.serial_no = %s
                AND se.docstatus = 1
                ORDER BY se.posting_date DESC LIMIT 1
            """, (serial_no,))
            if purchase_rate:
                rate = flt(purchase_rate[0][0])

        warehouse = frappe.db.get_value("Warehouse", {}, "name")

        se = frappe.new_doc("Stock Entry")
        se.entry_type = "Корректировка"
        se.posting_date = today()
        se.warehouse = warehouse
        se.remarks = f"Текущий остаток: {name} ({serial_no})"

        item_data = {
            "product": product.name,
            "product_name": product.product_name,
            "quantity": 1,
            "rate": rate,
            "amount": rate,
            "serial_no": serial_no,
        }
        if color:
            item_data["color"] = color
        if condition_raw:
            mapped = "Новый" if condition_raw.upper() == "NEW" else "Б/У"
            item_data["condition"] = mapped

        se.append("items", item_data)
        se.flags.ignore_permissions = True
        se.flags.ignore_validate = True
        se.flags.ignore_mandatory = True
        se.insert()
        se.submit()
        created += 1

    frappe.db.commit()
    print(f"Created {created} inventory adjustment entries, skipped {skipped} duplicates.")
    return created


@frappe.whitelist()
def repair_normalize_frequencies():
    """Fix old RU-only frequency values to bilingual Select options."""
    updates = {
        "Ежемесячно": "Ежемесячно (Monthly)",
        "Еженедельно": "Еженедельно (Weekly)",
        "Раз в две недели": "Раз в две недели (Biweekly)",
    }
    total = 0
    for old_val, new_val in updates.items():
        cnt = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabInstallment Plan` WHERE frequency = %s",
            (old_val,),
        )[0][0]
        if cnt:
            frappe.db.sql(
                "UPDATE `tabInstallment Plan` SET frequency = %s WHERE frequency = %s",
                (new_val, old_val),
            )
            total += cnt
    frappe.db.commit()
    print(f"Normalized {total} frequency values to bilingual format.")
    return total


@frappe.whitelist()
def repair_all():
    """Run all data repair steps in sequence."""
    print("=" * 60)
    print("STEP 0: Normalize frequency values")
    print("=" * 60)
    repair_normalize_frequencies()

    print("\n" + "=" * 60)
    print("STEP 1: Delete ghost Installment Plans")
    print("=" * 60)
    repair_delete_ghost_installment_plans()

    print("\n" + "=" * 60)
    print("STEP 2: Fix Installment Plan counts & schedules")
    print("=" * 60)
    repair_installment_plan_counts()

    print("\n" + "=" * 60)
    print("STEP 3: Populate Product color/storage from purchase.csv")
    print("=" * 60)
    repair_product_color_storage()

    print("\n" + "=" * 60)
    print("STEP 4: Import stock_entry.csv as inventory adjustments")
    print("=" * 60)
    repair_import_stock_inventory()

    print("\n" + "=" * 60)
    print("ALL REPAIRS COMPLETE")
    print("=" * 60)
