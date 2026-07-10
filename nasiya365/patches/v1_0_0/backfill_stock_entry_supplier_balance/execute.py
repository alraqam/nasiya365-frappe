import frappe


def execute():
    """Backfill total_value/balance_due for existing submitted Поступление
    Stock Entries.

    Two independent data gaps, both only affecting historical/imported
    records (anything saved through the form since always runs
    calculate_totals() on validate()):

    1. total_value/total_expense were found to be 0 on every single existing
       receiving entry despite Stock Entry Item rows carrying real
       rate/amount data -- the parent's denormalized total was apparently
       never (re)computed for imported records. Recompute it directly from
       item sums first, since balance_due below derives from it.
    2. The new columns (paid_amount, balance_due, payment_status) get their
       schema DEFAULT (0, 0, 'Не оплачено') applied to every existing row
       when the column is added -- but balance_due=0 incorrectly reads as
       "fully paid" for purchases nothing has ever been paid against. Reset
       balance_due to (now-correct) total_value so the "Suppliers Payable"
       report and Stock Entry list reflect real outstanding debt.

    Guarded by paid_amount=0 so re-running after real Supplier Payments
    exist doesn't reset an already-partially-paid entry's balance.
    """
    frappe.db.sql(
        """
        UPDATE `tabStock Entry` se
        INNER JOIN (
            SELECT parent, SUM(amount) AS items_total, SUM(expense) AS items_expense
            FROM `tabStock Entry Item`
            GROUP BY parent
        ) totals ON totals.parent = se.name
        SET se.total_value = totals.items_total,
            se.total_expense = totals.items_expense
        WHERE se.docstatus = 1
          AND se.entry_type = 'Поступление'
          AND se.total_value != totals.items_total
        """
    )
    frappe.db.sql(
        """
        UPDATE `tabStock Entry`
        SET balance_due = total_value,
            payment_status = 'Не оплачено'
        WHERE docstatus = 1
          AND entry_type = 'Поступление'
          AND IFNULL(paid_amount, 0) = 0
        """
    )
    frappe.db.commit()
