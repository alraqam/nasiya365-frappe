# Data Import Tool — CSV Check Report

Based on CSV files in `C:\Users\Furkat Ubaydullaev\Desktop\macintosh`.

---

## 1. File ↔ Import Type Mapping

| CSV File | Import Type (Тип импорта) | Status |
|----------|---------------------------|--------|
| `clients.csv` | **Импорт клиентов** | ✅ Compatible (see notes) |
| `suppliers.csv` | **Импорт поставщиков** | ✅ Compatible |
| `purchase.csv` | **Импорт закупок** | ✅ Compatible |
| `stock_entry.csv` | **Импорт складских записей** | ✅ Compatible (empty rows now skipped) |
| `installment_contracts.csv` | **Импорт договоров** | ✅ Compatible (see notes) |
| `installment_payments.csv` | **Импорт платежей** | ✅ Compatible |

---

## 2. Per-File Details

### 2.1 `clients.csv`

- **Headers:** Название клиента, Телефон 1, Телефон 2, Телефон, Телефон, Серия паспорта, Пол, Дата рожд., Адрес, Иш жойи, Доп. комментарий Comment, Место рождения, Сальдо счета  
- **Used by importer:** Название клиента, Телефон 1, Телефон 2, Телефон, Серия паспорта, Дата рожд., Адрес, Иш жойи  

**Issue:** Duplicate column name **«Телефон»** (appears twice).  
`csv.DictReader` keeps only the last column for a repeated key, so the **first** «Телефон» column is ignored.  
**Recommendation:** In the source export, rename one of them (e.g. «Телефон» and «Телефон 2» already used; use «Телефон доп» for the second) or remove the duplicate so both values are not lost.

**Date/number format:** Дата рожд. as `DD.MM.YY` and numbers with `,` as decimal and spaces (e.g. Сальдо) are handled by `parse_date` / `parse_number`.

---

### 2.2 `suppliers.csv`

- **Headers:** Название клиента, Телефон 1, Телефон, Do'kon raqami, Сальдо счета  
- **Used by importer:** Название клиента, Телефон 1, Телефон  

All required fields are present. «Do'kon raqami» and «Сальдо счета» are not used by the import (no change needed for compatibility).

---

### 2.3 `purchase.csv`

- **Headers:** Дата покупки, Код товара, Махсулот, Имейка, Таннарх, Поставщик, Коробка, Ёмкость батареи, Память, Бренд, Состояние, Цвет  
- **Used by importer:** Поставщик, Дата покупки, Код товара, Махсулот, Таннарх, Имейка  

All required columns exist.  
**Date format:** e.g. `8.2.26`, `24.01.26` — single-digit day/month are normalized by `parse_date`.  
**Number format:** «Таннарх» like `"1 080,000"` is handled by `parse_number` (spaces removed, comma → dot).

---

### 2.4 `stock_entry.csv`

- **Headers:** Код товара, Наименование товара, Состояние, Цвет, Бренд, Серийный номер  
- **Used by importer:** Код товара, Наименование товара, Состояние, Цвет, Бренд, Серийный номер  

Fully aligned.  
**Empty rows:** Many rows are empty (only commas). The importer now **skips** these and counts them as **«Пропущено»** instead of «Успешно». No need to delete empty lines from the file.

---

### 2.5 `installment_contracts.csv` (Импорт договоров)

- **Headers:** Номер документа, Внутренний номер, Дата продажи, Клиент, Телефон, Код товара, Наименование товара, Цена продажи, Количество, IMEI, Общая сумма, Оплачено, Остаток долга  
- **Used by importer:** Номер документа, Клиент, Телефон, Наименование товара, Код товара, IMEI, Цена продажи, Дата продажи, Общая сумма, Оплачено, Остаток долга  

All required fields are present.  
**What gets created:**
- **Sales Order** (with legacy document number stored in `po_no`)
- **Installment Plan** (if there is remaining debt > 0)
- **Contract** document (linked to customer, sales order, and installment plan)

**Missing column:** **«Количество платежей»** is not in this CSV. The importer uses it for `Installment Plan.number_of_installments`. If it is missing, the code defaults to `1` installment.  
**Recommendation:** If your export can add «Количество платежей» to the contracts CSV (same as in `installment_payments.csv`), add it so installment plans reflect the real number of payments.

---

### 2.6 `installment_payments.csv` (Импорт платежей)

- **Headers:** Номер документа, Внутренний номер, Дата продажи, Клиент, Телефон, Код товара, Наименование товара, Цена продажи, Количество, IMEI, Количество платежей, Общая сумма рассрочки, **Всего оплачено**, Общий остаток долга, Сделано платежей, **Последний платеж**, Детали всех платежей  
- **Used by importer:** Номер документа, Всего оплачено (fallback: Оплачено), Последний платеж (for payment date), Дата продажи (fallback for date)  

**Behaviour:**  
- One **Payment Transaction** is created per row with amount = **«Всего оплачено»** (total paid for that contract).  
- **Payment date** is taken from **«Последний платеж»** (date part before `=`) when present, otherwise from **«Дата продажи»**.  

**Note:** The importer does **not** create multiple Payment Transactions from «Детали всех платежей»; it only creates one summary payment per contract. To import each payment separately, the import logic would need to be extended to parse that column.

---

## 3. Import Order

For correct references (e.g. payments → sales orders), run imports in this order:

1. **Импорт клиентов** — `clients.csv`  
2. **Импорт поставщиков** — `suppliers.csv`  
3. **Импорт складских записей** and/or **Импорт закупок** (optional, for stock)  
4. **Импорт договоров** — `installment_contracts.csv` (creates Sales Orders with `po_no` = Номер документа)  
5. **Импорт платежей** — `installment_payments.csv` (links by Номер документа to Sales Order)

---

## 4. Code Changes Made

1. **SkipRow handling**  
   Rows that are empty or invalid (e.g. no name, no document number, no code/name in stock) now raise `SkipRow` and are counted as **«Пропущено»**, not «Успешно» or «Ошибки».

2. **Payment date**  
   Payment Transaction uses **«Последний платеж»** (date before `=`) when present, otherwise **«Дата продажи»**, instead of always using current time.

3. **Summary message**  
   The final import message now includes **«Пропущено»** (skipped count).

---

## 5. Quick Reference — Expected Column Names

| Import Type | Required / important columns |
|-------------|------------------------------|
| Импорт клиентов | Название клиента, Телефон 1 / Телефон 2 / Телефон, Серия паспорта, Дата рожд., Адрес, Иш жойи |
| Импорт поставщиков | Название клиента (supplier name), Телефон 1 / Телефон |
| Импорт закупок | Поставщик, Дата покупки, Код товара, Махсулот, Таннарх, Имейка |
| Импорт складских записей | Код товара, Наименование товара, Состояние, Цвет, Бренд, Серийный номер |
| Импорт договоров | Номер документа, Дата продажи, Клиент, Телефон, Код товара, Наименование товара, IMEI, Цена продажи, Общая сумма, Оплачено, Остаток долга; optional: Количество платежей |
| Импорт платежей | Номер документа, Всего оплачено (or Оплачено); optional: Последний платеж, Дата продажи |

All CSVs in `macintosh` are compatible with the Data Import Tool; the main improvements are correct skip counting, payment date from CSV, and the notes above for duplicate columns and optional «Количество платежей».
