# C5 — списанный план даёт прибыль — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Списанный (`status="Списан"`) план перестаёт давать прибыль в движках cash-basis и cost-recovery (консистентно с accrual/Sales Report/Collections).

**Architecture:** Добавить условие `plan.status == "Списан"` в ветку Installment Plan у `_compute_cash` и `_compute_cost_recovery` в profit.py, той же `or`-формой, что уже используется для `contract_status == "Отменен"`.

**Tech Stack:** Frappe v16, Python, MariaDB. Тесты — `unittest.TestCase` + SAVEPOINT, изолированный период 2030 (вне demo-данных), запуск `bench run-tests`.

## Global Constraints

- Фильтровать `status == "Списан"` в обоих движках; `_compute_accrual` НЕ трогать (уже исключает через allow-list `_LIVE_PLAN_STATUSES`).
- В `_compute_cost_recovery` добавить `"status"` в `get_value` (там его нет); в `_compute_cash` `status` уже выбирается.
- Тестовые даты — 2030 (dev demo-данные тянутся 2026-06-05..2026-10-20; SAVEPOINT их не прячет, `_compute_cash` агрегирует всю БД за период).
- Не менять суммы/график/Installment Plan путь.
- Запуск тестов: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module <mod>`.

---

### Task 1: Фильтр списанных планов в движках + тесты

**Files:**
- Modify: `nasiya365/api/profit.py` (`_compute_cash` и `_compute_cost_recovery` — ветка Installment Plan)
- Create: `nasiya365/tests/test_c5_writeoff_profit.py`

**Interfaces:**
- Produces (тест-хелперы): `_db_insert`, `_seed_plan(status="Активный", ...)`, `_seed_payment(rn, amount, date)`

- [ ] **Step 1: Написать падающий тест (создать файл)**

```python
# nasiya365/tests/test_c5_writeoff_profit.py
import unittest
import frappe

from nasiya365.api.profit import _compute_cash, _compute_cost_recovery


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_plan(status="Активный"):
    return _db_insert(
        "Installment Plan", imei="C5" + frappe.generate_hash(length=6),
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2030-01-01",
        status=status, contract_status="Подписан", docstatus=1,
    )


def _seed_payment(plan_name, amount, date):
    return _db_insert(
        "Payment Transaction", reference_doctype="Installment Plan",
        reference_name=plan_name, amount=amount, payment_date=date,
        status="Завершен", docstatus=1)


class TestC5WriteoffProfit(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("c5_test")

    def tearDown(self):
        frappe.db.rollback(save_point="c5_test")

    def test_writeoff_plan_excluded_from_cash(self):
        plan = _seed_plan(status="Списан")
        _seed_payment(plan.name, 350, "2030-01-10")
        r = _compute_cash("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["financed_revenue"], 0.0, places=2)  # written-off excluded
        self.assertAlmostEqual(r["financed_margin"], 0.0, places=2)

    def test_active_plan_still_counted(self):
        plan = _seed_plan(status="Активный")
        _seed_payment(plan.name, 350, "2030-01-10")
        r = _compute_cash("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["financed_revenue"], 350.0, places=2)  # active still counts

    def test_writeoff_plan_excluded_from_cost_recovery(self):
        plan = _seed_plan(status="Списан")
        _seed_payment(plan.name, 350, "2030-01-10")
        r = _compute_cost_recovery("2030-01-01", "2030-01-31", None)
        self.assertAlmostEqual(r["financed_margin"], 0.0, places=2)  # recognized margin = 0
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_c5_writeoff_profit`
Expected: FAIL — `test_writeoff_plan_excluded_from_cash` (financed_revenue = 350, списанный ещё считается).

- [ ] **Step 3: Реализовать фильтр**

В `nasiya365/api/profit.py`, `_compute_cash`, ветка Installment Plan — заменить:

```python
            if not plan or (plan.contract_status == "Отменен"):
                continue
```

на:

```python
            if not plan or plan.contract_status == "Отменен" or plan.status == "Списан":
                continue
```

В `_compute_cost_recovery`, ветка Installment Plan — заменить:

```python
                plan = frappe.db.get_value(
                    "Installment Plan", w.rn,
                    ["imei", "principal_amount", "financed_amount", "total_interest",
                     "total_amount", "contract_status", "stock_entry", "start_date"],
                    as_dict=True)
                plan_cache[w.rn] = plan
            if not plan or plan.contract_status == "Отменен":
                continue
```

на:

```python
                plan = frappe.db.get_value(
                    "Installment Plan", w.rn,
                    ["imei", "principal_amount", "financed_amount", "total_interest",
                     "total_amount", "contract_status", "status", "stock_entry", "start_date"],
                    as_dict=True)
                plan_cache[w.rn] = plan
            if not plan or plan.contract_status == "Отменен" or plan.status == "Списан":
                continue
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_c5_writeoff_profit`
Expected: PASS (3 теста).

- [ ] **Step 5: Регресс движка**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_cost_recovery_engine`
Expected: PASS (4/4).

- [ ] **Step 6: Коммит**

```bash
git add nasiya365/api/profit.py nasiya365/tests/test_c5_writeoff_profit.py
git commit -m "fix(pnl): списанные планы (Списан) не дают прибыль в cash/cost-recovery"
```

---

## После завершения

- Ветка `fix/c5-writeoff-profit` (спека уже там).
- Мерж в `main` + пуш — только по явному разрешению; деплой делает пользователь сам.
