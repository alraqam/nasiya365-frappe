import frappe


def execute():
    """Point Installment Plan.stock_entry at a Stock Entry Item row when it still stores the parent STE name."""
    rows = frappe.db.sql(
        """
        SELECT name, stock_entry FROM `tabInstallment Plan`
        WHERE IFNULL(TRIM(stock_entry), '') != ''
        """,
        as_dict=True,
    )
    for row in rows or []:
        ref = (row.stock_entry or "").strip()
        if not ref:
            continue
        if frappe.db.exists("Stock Entry Item", ref):
            continue
        if not frappe.db.exists("Stock Entry", ref):
            continue
        plan_name = row.name
        imei_hint = (frappe.db.get_value("Installment Plan", plan_name, "imei") or "").strip()
        sei_name = None
        if imei_hint:
            tail = imei_hint[-6:] if len(imei_hint) >= 6 else imei_hint
            candidates = frappe.get_all(
                "Stock Entry Item",
                filters={"parent": ref},
                fields=["name", "imei"],
                order_by="idx asc",
            )
            norm_tail = tail.upper().replace(" ", "")
            for c in candidates:
                im = (c.get("imei") or "").strip()
                if not im:
                    continue
                n = im.upper().replace(" ", "")
                if n == norm_tail or (len(n) >= 6 and len(norm_tail) >= 6 and n[-6:] == norm_tail[-6:]):
                    sei_name = c.name
                    break
        if not sei_name:
            sei_name = frappe.db.sql(
                """
                SELECT name FROM `tabStock Entry Item`
                WHERE parent = %s
                ORDER BY idx ASC
                LIMIT 1
                """,
                (ref,),
            )
            sei_name = sei_name[0][0] if sei_name else None
        if sei_name:
            frappe.db.set_value(
                "Installment Plan",
                plan_name,
                "stock_entry",
                sei_name,
                update_modified=False,
            )
