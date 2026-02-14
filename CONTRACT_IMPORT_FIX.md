# Contract Import Fix - Summary

## Issue
The Data Import Tool's "Импорт договоров" (Import Contracts) function was **NOT creating Contract documents**. It was only creating:
- ✅ Sales Orders
- ✅ Installment Plans (when debt > 0)
- ❌ **Contract documents** (missing!)

This meant that when you imported contracts from `installment_contracts.csv`, the Contract DocType records were never created in the system.

## Solution
Modified `nasiya365/data_import.py` to automatically create Contract documents during the "Импорт договоров" import process.

### Changes Made

#### 1. **Updated `process_row()` function** (lines 261-281)
Now creates a Contract document after creating the Sales Order and Installment Plan:

```python
# 5. Create Contract document
contract = frappe.new_doc("Contract")
contract.contract_type = "Рассрочка (BNPL)"
contract.customer = customer.name
contract.sales_order = so.name
contract.installment_plan = installment_plan.name if installment_plan else None
contract.total_amount = total_amount
contract.contract_date = sale_date
contract.status = "Активный" if remaining_debt > 0 else "Завершен"

contract.flags.ignore_permissions = True
if skip_validation:
    contract.flags.ignore_validate = True
    contract.flags.ignore_mandatory = True

contract.insert()
```

#### 2. **Updated `create_installment_plan()` function** (line 365)
Added return statement so the created plan can be referenced when creating the Contract:

```python
return plan
```

#### 3. **Updated Documentation** 
Modified `DATA_IMPORT_CSV_REPORT.md` to clarify what documents get created during import.

## What Now Gets Created

When you import contracts using "Импорт договоров", the system will create:

1. **Sales Order** 
   - Linked to customer
   - Contains the product/item
   - Stores the original document number in `po_no` field

2. **Installment Plan** (if `Остаток долга` > 0)
   - Linked to customer and sales order
   - Contains payment schedule
   - Status: "Active"

3. **Contract Document** (NEW!)
   - Type: "Рассрочка (BNPL)"
   - Linked to customer, sales order, and installment plan
   - Status: "Активный" (if debt remains) or "Завершен" (if fully paid)
   - Stores the total amount and contract date

## Testing the Fix

To test the updated import:

1. Open the Data Import Tool in Frappe
2. Select "Импорт договоров" as the import type
3. Upload your `installment_contracts.csv` file
4. Run the import
5. Check that Contract documents are now created in addition to Sales Orders and Installment Plans

## File Changes
- `nasiya365/data_import.py` - Modified contract import logic
- `DATA_IMPORT_CSV_REPORT.md` - Updated documentation

## Date
2026-02-13
