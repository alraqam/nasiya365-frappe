# Обязательный IMEI при приёме телефонов — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** При приёме (`entry_type='Поступление'`) телефона (`Product.allow_installment=1`) с пустым IMEI — блокировать сохранение, чтобы «безымянный» телефон нельзя было создать.

**Architecture:** Новый метод `StockEntry._require_imei_for_phones()` в `stock_entry.py`, вызывается в `validate()`. Только обязательность IMEI (без уникальности — из-за выкупа обратно).

**Tech Stack:** Frappe v16, Python. Тесты — `unittest.TestCase` + SAVEPOINT, запуск `bench run-tests`.

## Global Constraints

- Критерий «телефон» = `Product.allow_installment == 1`.
- Проверять только `entry_type == "Поступление"`.
- Аксессуары (`allow_installment=0`) и типы Отпуск/Перемещение/Корректировка — НЕ трогать.
- Уникальность IMEI НЕ проверять.
- Сообщение (verbatim): `Укажите IMEI для телефона «{0}» (строка {1}). Без IMEI телефон невозможно списать со склада при продаже.`
- Перевод через `frappe._(...)` (в файле `import frappe`, отдельного `_` нет).
- Запуск тестов: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_require_imei_receipt`.

---

### Task 1: Проверка IMEI при приёме + тесты

**Files:**
- Modify: `nasiya365/nasiya365/doctype/stock_entry/stock_entry.py` (метод + вызов в `validate`)
- Create: `nasiya365/tests/test_require_imei_receipt.py`

**Interfaces:**
- Produces: `StockEntry._require_imei_for_phones(self)` — бросает `ValidationError` при телефоне без IMEI в поступлении.

- [ ] **Step 1: Написать падающий тест (создать файл)**

```python
# nasiya365/tests/test_require_imei_receipt.py
import unittest
import frappe


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _product(allow_installment):
    return _db_insert("Product", product_name="T-" + frappe.generate_hash(length=5),
                      allow_installment=allow_installment, is_active=1)


def _ste(entry_type, rows):
    """In-memory Stock Entry (не сохраняется) для прямого вызова проверки.
    rows = список dict со ссылкой на product и imei."""
    doc = frappe.get_doc({"doctype": "Stock Entry", "entry_type": entry_type})
    for i, r in enumerate(rows, start=1):
        doc.append("items", {"product": r["product"], "imei": r.get("imei", ""),
                             "quantity": 1, "idx": i})
    return doc


class TestRequireImeiReceipt(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("req_imei")

    def tearDown(self):
        frappe.db.rollback(save_point="req_imei")

    def test_phone_receipt_without_imei_throws(self):
        phone = _product(allow_installment=1)
        doc = _ste("Поступление", [{"product": phone.name, "imei": ""}])
        with self.assertRaises(frappe.exceptions.ValidationError):
            doc._require_imei_for_phones()

    def test_phone_receipt_with_imei_ok(self):
        phone = _product(allow_installment=1)
        doc = _ste("Поступление", [{"product": phone.name, "imei": "353898106937998"}])
        doc._require_imei_for_phones()  # без исключения

    def test_accessory_receipt_without_imei_ok(self):
        acc = _product(allow_installment=0)
        doc = _ste("Поступление", [{"product": acc.name, "imei": ""}])
        doc._require_imei_for_phones()  # без исключения

    def test_non_receipt_type_not_checked(self):
        phone = _product(allow_installment=1)
        doc = _ste("Отпуск", [{"product": phone.name, "imei": ""}])
        doc._require_imei_for_phones()  # не Поступление → не блокируем

    def test_mixed_rows_phone_ok_accessory_empty_ok(self):
        phone = _product(allow_installment=1)
        acc = _product(allow_installment=0)
        doc = _ste("Поступление", [
            {"product": phone.name, "imei": "353898106937998"},
            {"product": acc.name, "imei": ""},
        ])
        doc._require_imei_for_phones()  # телефон с IMEI, аксессуар без — ОК
```

- [ ] **Step 2: Запустить — убедиться, что падает (AttributeError)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_require_imei_receipt`
Expected: FAIL — `'StockEntry' object has no attribute '_require_imei_for_phones'`.

- [ ] **Step 3: Реализовать метод + вызов**

В `nasiya365/nasiya365/doctype/stock_entry/stock_entry.py`, класс `StockEntry`, добавить вызов в `validate()`:

```python
	def validate(self):
		self.calculate_totals()
		self.set_items_summary()
		self.set_business_status()
		self.set_payment_status()
		self._require_imei_for_phones()
```

Добавить метод (например сразу после `validate`):

```python
	def _require_imei_for_phones(self):
		"""Телефон (Product.allow_installment=1) при приёме обязан иметь IMEI — иначе
		его невозможно списать со склада при продаже (сопоставление идёт по IMEI).
		Уникальность НЕ проверяем: тот же IMEI законно приходуется повторно при выкупе."""
		if (self.entry_type or "").strip() != "Поступление":
			return
		for item in self.items:
			if not item.product:
				continue
			if not frappe.get_cached_value("Product", item.product, "allow_installment"):
				continue
			if (item.imei or "").strip():
				continue
			product_name = frappe.get_cached_value("Product", item.product, "product_name") or item.product
			frappe.throw(frappe._(
				"Укажите IMEI для телефона «{0}» (строка {1}). "
				"Без IMEI телефон невозможно списать со склада при продаже."
			).format(product_name, item.idx))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_require_imei_receipt`
Expected: PASS (5 тестов).

- [ ] **Step 5: Коммит**

```bash
git add nasiya365/nasiya365/doctype/stock_entry/stock_entry.py nasiya365/tests/test_require_imei_receipt.py
git commit -m "feat(stock): IMEI обязателен при приёме телефонов (allow_installment)"
```

---

## Проверка на dev (после Task 1, вручную — опционально)

1. `docker compose restart backend`.
2. Создать поступление телефона (товар с рассрочкой) без IMEI → сохранение блокируется с сообщением.
3. С IMEI → сохраняется. Аксессуар без IMEI → сохраняется.

## После завершения

- Ветка `feat/require-imei-on-receipt` (спека уже там).
- Мерж в `main` + пуш — по разрешению; деплой делает пользователь сам.
