# C2 — отменённая наличная продажа — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Отменённая наличная продажа перестаёт давать фантомную прибыль: при отмене заказа отменяется его платёж (going forward), а движки P&L игнорируют отменённые заказы (защита старых данных).

**Architecture:** (1) `SalesOrder.on_cancel` каскадно отменяет связанный проведённый Payment Transaction (реверс кассы/разноски — уже в Payment Transaction.on_cancel). (2) `_compute_cash` и `_compute_cost_recovery` в profit.py пропускают Sales Order с `docstatus=2`. Старые платежи физически не отменяются.

**Tech Stack:** Frappe v16, Python, MariaDB. Тесты — `unittest.TestCase` + SAVEPOINT (паттерн `nasiya365/tests/test_cost_recovery_engine.py`), запуск `bench run-tests`.

## Global Constraints

- Пропускать в движках только `docstatus == 2` (отменённые). docstatus=1 считается как раньше.
- Каскадная отмена: только проведённые платежи (`docstatus=1`) заказа; идемпотентно (нет платежа / уже отменён → без ошибки); `pt.flags.ignore_permissions = True` перед `pt.cancel()`.
- НЕ трогать: `_compute_accrual` (уже фильтрует docstatus=1), Installment Plan путь, суммы/график, старые висящие платежи (физически).
- Запуск тестов: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module <mod>`.

---

### Task 1: docstatus-фильтр в движках P&L + тесты

**Files:**
- Modify: `nasiya365/api/profit.py` (`_compute_cash` и `_compute_cost_recovery` — ветка Sales Order)
- Create: `nasiya365/tests/test_c2_cancelled_sale.py`

**Interfaces:**
- Produces (тест-хелперы): `_db_insert`, `_seed_sales_order(total, order_date, docstatus=1, branch=None)`, `_seed_payment(rdt, rn, amount, date, docstatus=1, status="Завершен")`

- [ ] **Step 1: Написать падающий тест (создать файл)**

```python
# nasiya365/tests/test_c2_cancelled_sale.py
import unittest
import frappe

from nasiya365.api.profit import _compute_cash


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_sales_order(total, order_date, docstatus=1, branch=None):
    return _db_insert("Sales Order", total_amount=total, order_date=order_date,
                      branch=branch, docstatus=docstatus)


def _seed_payment(rdt, rn, amount, date, docstatus=1, status="Завершен"):
    return _db_insert("Payment Transaction", reference_doctype=rdt, reference_name=rn,
                      amount=amount, payment_date=date, status=status, docstatus=docstatus)


class TestC2CancelledSale(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("c2_test")

    def tearDown(self):
        frappe.db.rollback(save_point="c2_test")

    def test_cancelled_so_excluded_from_cash(self):
        # both have a live payment in the window; only the docstatus=1 SO counts
        ok = _seed_sales_order(800, "2026-09-05", docstatus=1)
        cancelled = _seed_sales_order(800, "2026-09-06", docstatus=2)
        _seed_payment("Sales Order", ok.name, 800, "2026-09-10")
        _seed_payment("Sales Order", cancelled.name, 800, "2026-09-11")
        r = _compute_cash("2026-09-01", "2026-09-30", None)
        self.assertAlmostEqual(r["cash_revenue"], 800.0, places=2)  # cancelled one excluded

    def test_normal_so_still_counted(self):
        ok = _seed_sales_order(500, "2026-09-05", docstatus=1)
        _seed_payment("Sales Order", ok.name, 500, "2026-09-10")
        r = _compute_cash("2026-09-01", "2026-09-30", None)
        self.assertAlmostEqual(r["cash_revenue"], 500.0, places=2)

    def test_cost_recovery_excludes_cancelled_so(self):
        from nasiya365.api.profit import _compute_cost_recovery
        cancelled = _seed_sales_order(800, "2026-09-06", docstatus=2)
        _seed_payment("Sales Order", cancelled.name, 800, "2026-09-11")
        r = _compute_cost_recovery("2026-09-01", "2026-09-30", None)
        self.assertAlmostEqual(r["cash_margin"], 0.0, places=2)  # recognized margin from cancelled = 0
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_c2_cancelled_sale`
Expected: FAIL — `test_cancelled_so_excluded_from_cash` (cash_revenue = 1600, отменённый ещё считается).

- [ ] **Step 3: Реализовать docstatus-фильтр**

В `nasiya365/api/profit.py`, в `_compute_cash`, ветка `elif pay.rdt == "Sales Order":` — заменить:

```python
            so = so_cache.get(pay.rn)
            if so is None:
                so = frappe.db.get_value(
                    "Sales Order", pay.rn, ["name", "total_amount"], as_dict=True
                )
                so_cache[pay.rn] = so
            if not so:
                continue
```

на:

```python
            so = so_cache.get(pay.rn)
            if so is None:
                so = frappe.db.get_value(
                    "Sales Order", pay.rn, ["name", "total_amount", "docstatus"], as_dict=True
                )
                so_cache[pay.rn] = so
            if not so or so.docstatus == 2:
                continue
```

В `_compute_cost_recovery`, ветка `elif w.rdt == "Sales Order":` — заменить:

```python
            so = so_cache.get(w.rn)
            if so is None:
                so = frappe.db.get_value(
                    "Sales Order", w.rn, ["name", "total_amount"], as_dict=True)
                so_cache[w.rn] = so
            if not so:
                continue
```

на:

```python
            so = so_cache.get(w.rn)
            if so is None:
                so = frappe.db.get_value(
                    "Sales Order", w.rn, ["name", "total_amount", "docstatus"], as_dict=True)
                so_cache[w.rn] = so
            if not so or so.docstatus == 2:
                continue
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_c2_cancelled_sale`
Expected: PASS (3 теста).

- [ ] **Step 5: Регресс движка**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_cost_recovery_engine`
Expected: PASS (4/4 — нормальные docstatus=1 SO считаются как раньше).

- [ ] **Step 6: Коммит**

```bash
git add nasiya365/api/profit.py nasiya365/tests/test_c2_cancelled_sale.py
git commit -m "fix(pnl): движки не считают отменённые заказы (docstatus=2)"
```

---

### Task 2: Каскадная отмена платежа в `SalesOrder.on_cancel`

**Files:**
- Modify: `nasiya365/nasiya365/doctype/sales_order/sales_order.py` (`_cancel_linked_cash_receipt` + вызов в `on_cancel`)
- Modify: `nasiya365/tests/test_c2_cancelled_sale.py` (тесты каскада)

**Interfaces:**
- Consumes: `_db_insert`, `_seed_sales_order`, `_seed_payment` (Task 1).
- Produces: `SalesOrder._cancel_linked_cash_receipt(self)`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `TestC2CancelledSale`:

```python
    def test_cancel_linked_cash_receipt(self):
        so = _seed_sales_order(800, "2026-09-05", docstatus=1)
        pt = _seed_payment("Sales Order", so.name, 800, "2026-09-05")   # docstatus=1
        frappe.get_doc("Sales Order", so.name)._cancel_linked_cash_receipt()
        self.assertEqual(frappe.db.get_value("Payment Transaction", pt.name, "docstatus"), 2)

    def test_cancel_receipt_idempotent_no_payment(self):
        so = _seed_sales_order(800, "2026-09-05", docstatus=1)          # нет платежа
        frappe.get_doc("Sales Order", so.name)._cancel_linked_cash_receipt()   # без ошибки

    def test_cancel_receipt_skips_already_cancelled(self):
        so = _seed_sales_order(800, "2026-09-05", docstatus=1)
        _seed_payment("Sales Order", so.name, 800, "2026-09-05", docstatus=2, status="Отменен")
        frappe.get_doc("Sales Order", so.name)._cancel_linked_cash_receipt()   # без ошибки (нечего отменять)
```

- [ ] **Step 2: Запустить — убедиться, что падает (AttributeError)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_c2_cancelled_sale`
Expected: FAIL — `'SalesOrder' object has no attribute '_cancel_linked_cash_receipt'`.

- [ ] **Step 3: Реализовать метод + вызов**

В `nasiya365/nasiya365/doctype/sales_order/sales_order.py`, в `on_cancel` — добавить вызов последней строкой:

```python
    def on_cancel(self):
        self.status = "Отменен"
        self.db_update()
        self.reverse_stock()
        self._cancel_linked_cash_receipt()
```

Добавить метод в класс (например после `on_cancel`):

```python
    def _cancel_linked_cash_receipt(self):
        """Отменить проведённый наличный платёж этого заказа (Payment Transaction.on_cancel
        реверсирует кассу и разноску). Идемпотентно: отсутствующие/уже отменённые — пропуск."""
        names = frappe.get_all(
            "Payment Transaction",
            filters={"reference_doctype": "Sales Order",
                     "reference_name": self.name, "docstatus": 1},
            pluck="name",
        )
        for n in names:
            pt = frappe.get_doc("Payment Transaction", n)
            pt.flags.ignore_permissions = True
            pt.cancel()
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_c2_cancelled_sale`
Expected: PASS (6 тестов).

- [ ] **Step 5: Коммит**

```bash
git add nasiya365/nasiya365/doctype/sales_order/sales_order.py nasiya365/tests/test_c2_cancelled_sale.py
git commit -m "fix(sales): отмена заказа отменяет его наличный платёж (реверс кассы)"
```

---

## Проверка на dev (после Task 2, вручную — опционально)

1. `docker compose restart backend` (ждать готовности).
2. Создать наличную продажу (submit → создаётся платёж), затем отменить заказ →
   платёж становится «Отменен», касса реверсирована.
3. P&L за период → отменённая продажа не даёт прибыли; Sales Report и P&L сходятся.

## После завершения

- Ветка `fix/c2-cancelled-sale` (спека уже там).
- Мерж в `main` + пуш — только по явному разрешению; деплой делает пользователь сам.
