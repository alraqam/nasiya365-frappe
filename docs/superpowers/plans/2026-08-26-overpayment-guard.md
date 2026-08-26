# Гард переплаты по рассрочке — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Блокировать платёж по рассрочке, превышающий остаток договора, ещё до проведения — с показом точного остатка.

**Architecture:** Серверный гард в `PaymentTransaction.validate()`. Резолвит связанный Installment Plan (напрямую или через Sales Order), берёт остаток (header `remaining_balance`, при NULL/0 — из графика) и `frappe.throw`, если сумма превышает остаток больше чем на копеечный допуск. Существующая пост-обработка `excess` в `on_submit` остаётся страховкой.

**Tech Stack:** Frappe v16, Python, MariaDB. Тесты — `unittest.TestCase` + SAVEPOINT (паттерн из `nasiya365/tests/test_cost_recovery_engine.py`), запуск через `bench run-tests`.

## Global Constraints

- Допуск переплаты: `_OVERPAYMENT_TOLERANCE = 0.01` (модульная константа).
- Байпас гарда в контексте: `frappe.flags.in_migrate` / `in_import` / `in_patch` / `in_install`.
- Охват — только рассрочка: reference = Installment Plan (напрямую) или Sales Order → связанный план. Наличные Sales Order без плана НЕ гардятся.
- Текст ошибки (verbatim, format-плейсхолдеры `{0}`=сумма, `{1}`=план, `{2}`=остаток):
  `Сумма платежа {0} превышает остаток по договору {1}. Остаток: {2} USD. Проверьте сумму — возможно, введена в сумах вместо USD. Для полного закрытия введите {2}.`
- НЕ менять: `apply_payment`, аллокацию, синк кассы, пост-обработку `excess` в `on_submit`.
- Все правки в одном файле контроллера: `nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py`.
- Запуск тестов: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_overpayment_guard`.

---

### Task 1: Хелпер `_resolve_installment_plan_name` + рефактор аллокации (DRY, без смены поведения)

**Files:**
- Modify: `nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py` (добавить модульную функцию; заменить инлайн-резолв в `allocate_payment_transaction_to_installment_plan`, строки ~157–167)
- Create: `nasiya365/tests/test_overpayment_guard.py` (общие хелперы + тест резолвера)

**Interfaces:**
- Produces: `_resolve_installment_plan_name(doc) -> str | None` — модульная функция.
- Produces (в тест-файле): `_db_insert(doctype, **fields)`, `_plan(remaining_balance=0, sales_order=None)`, `_sched_row(plan_name, idx, amount, paid_amount)`, `_pt(reference_doctype, reference_name, amount)`.

- [ ] **Step 1: Написать падающий тест (создать файл)**

```python
# nasiya365/tests/test_overpayment_guard.py
import unittest
import frappe

from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
    _resolve_installment_plan_name,
)


def _db_insert(doctype, **fields):
    """Вставка строки в обход validate()/hooks (паттерн test_cost_recovery_engine)."""
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _plan(remaining_balance=0, sales_order=None):
    return _db_insert(
        "Installment Plan",
        imei="TESTIMEI0001",
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2026-01-01",
        status="Активный", contract_status="Активный", docstatus=1,
        remaining_balance=remaining_balance, sales_order=sales_order,
    )


def _sched_row(plan_name, idx, amount, paid_amount):
    return _db_insert(
        "Installment Schedule",
        parent=plan_name, parenttype="Installment Plan", parentfield="schedule",
        idx=idx, installment_number=idx, amount=amount, paid_amount=paid_amount,
        due_date="2026-02-01", status="Ожидает",
    )


def _pt(reference_doctype, reference_name, amount):
    """In-memory Payment Transaction (без сохранения) для прямого вызова методов гарда."""
    return frappe.get_doc({
        "doctype": "Payment Transaction",
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "amount": amount,
        "payment_date": "2026-08-01",
    })


class TestOverpaymentGuard(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("nasiya_overpay_test")

    def tearDown(self):
        frappe.db.rollback(save_point="nasiya_overpay_test")

    # ── Task 1: resolver ─────────────────────────────
    def test_resolve_installment_plan_reference(self):
        plan = _plan(remaining_balance=100)
        self.assertEqual(_resolve_installment_plan_name(_pt("Installment Plan", plan.name, 1)), plan.name)

    def test_resolve_via_sales_order(self):
        so = _db_insert("Sales Order", total_amount=1200, order_date="2026-01-01", docstatus=1)
        plan = _plan(remaining_balance=100, sales_order=so.name)
        self.assertEqual(_resolve_installment_plan_name(_pt("Sales Order", so.name, 1)), plan.name)

    def test_resolve_none_for_cash_sales_order(self):
        so = _db_insert("Sales Order", total_amount=650, order_date="2026-01-01", docstatus=1)
        self.assertIsNone(_resolve_installment_plan_name(_pt("Sales Order", so.name, 1)))
```

- [ ] **Step 2: Запустить — убедиться, что падает (ImportError)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_overpayment_guard`
Expected: FAIL — `ImportError: cannot import name '_resolve_installment_plan_name'`.

- [ ] **Step 3: Реализовать хелпер + рефактор аллокации**

Добавить модульную функцию рядом с другими хелперами (перед `allocate_payment_transaction_to_installment_plan`):

```python
def _resolve_installment_plan_name(doc):
    """Installment Plan, к которому относится платёж, по его reference.

    reference = Installment Plan → сам; reference = Sales Order → план, связанный
    через `sales_order`; иначе (напр. наличный Sales Order) → None.
    """
    rd = (getattr(doc, "reference_doctype", "") or "").strip()
    rn = (getattr(doc, "reference_name", "") or "").strip()
    if rd == "Installment Plan" and rn:
        return rn
    if rd == "Sales Order" and rn:
        return frappe.db.get_value("Installment Plan", {"sales_order": rn}, "name")
    return None
```

В `allocate_payment_transaction_to_installment_plan` заменить блок:

```python
    rd = (doc.reference_doctype or "").strip()
    rn = (doc.reference_name or "").strip()

    plan_name = None
    if rd == "Installment Plan" and rn:
        plan_name = rn
    elif rd == "Sales Order" and rn:
        # Legacy/import/payment-from-SO flows: map SO -> linked installment plan.
        plan_name = frappe.db.get_value("Installment Plan", {"sales_order": rn}, "name")
    if not plan_name:
        return
```

на:

```python
    plan_name = _resolve_installment_plan_name(doc)
    if not plan_name:
        return
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_overpayment_guard`
Expected: PASS (3 теста).

- [ ] **Step 5: Коммит**

```bash
git add nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py nasiya365/tests/test_overpayment_guard.py
git commit -m "refactor(payment): общий резолвер плана из reference (DRY)"
```

---

### Task 2: Хелпер `_installment_plan_remaining`

**Files:**
- Modify: `nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py` (добавить модульную функцию)
- Modify: `nasiya365/tests/test_overpayment_guard.py` (добавить тесты остатка)

**Interfaces:**
- Consumes: `_db_insert`, `_plan`, `_sched_row` из Task 1.
- Produces: `_installment_plan_remaining(plan_name: str) -> float`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `TestOverpaymentGuard` (и импорт в шапке файла):

```python
# шапка файла — расширить существующий импорт:
from nasiya365.nasiya365.doctype.payment_transaction.payment_transaction import (
    _resolve_installment_plan_name,
    _installment_plan_remaining,
)
```

```python
    # ── Task 2: remaining ────────────────────────────
    def test_remaining_uses_header(self):
        plan = _plan(remaining_balance=165.91)
        self.assertAlmostEqual(_installment_plan_remaining(plan.name), 165.91, places=2)

    def test_remaining_falls_back_to_schedule_when_header_zero(self):
        plan = _plan(remaining_balance=0)
        _sched_row(plan.name, 1, amount=100, paid_amount=60)   # due = 40
        self.assertAlmostEqual(_installment_plan_remaining(plan.name), 40.0, places=2)

    def test_remaining_zero_for_closed_plan(self):
        plan = _plan(remaining_balance=0)                       # нет строк графика
        self.assertEqual(_installment_plan_remaining(plan.name), 0.0)
```

- [ ] **Step 2: Запустить — убедиться, что падает (ImportError)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_overpayment_guard`
Expected: FAIL — `ImportError: cannot import name '_installment_plan_remaining'`.

- [ ] **Step 3: Реализовать хелпер**

Добавить рядом с `_resolve_installment_plan_name`:

```python
def _installment_plan_remaining(plan_name: str) -> float:
    """Сколько ещё должны по договору. Primary — header `remaining_balance`; если он
    NULL или <= 0, берём из графика SUM(amount - paid_amount), чтобы устаревший header
    не давал ложный блок. Clamp >= 0."""
    if not plan_name:
        return 0.0
    header = flt(frappe.db.get_value("Installment Plan", plan_name, "remaining_balance"))
    if header > 0:
        return header
    due = frappe.db.sql(
        """SELECT COALESCE(SUM(COALESCE(amount, 0) - COALESCE(paid_amount, 0)), 0)
           FROM `tabInstallment Schedule` WHERE parent = %s""",
        (plan_name,),
    )[0][0]
    return max(flt(due), 0.0)
```

(`flt` уже импортирован в файле: `from frappe.utils import cint, flt, getdate, nowdate`.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_overpayment_guard`
Expected: PASS (6 тестов).

- [ ] **Step 5: Коммит**

```bash
git add nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py nasiya365/tests/test_overpayment_guard.py
git commit -m "feat(payment): хелпер остатка по договору (header + fallback из графика)"
```

---

### Task 3: Гард `_guard_installment_overpayment` + подключение в `validate()`

**Files:**
- Modify: `nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py` (константа `_OVERPAYMENT_TOLERANCE`; метод в классе `PaymentTransaction`; вызов в `validate`)
- Modify: `nasiya365/tests/test_overpayment_guard.py` (сценарии гарда)

**Interfaces:**
- Consumes: `_resolve_installment_plan_name`, `_installment_plan_remaining`, `_pt`, `_plan`, `_sched_row`.
- Produces: `PaymentTransaction._guard_installment_overpayment(self)` — бросает `frappe.exceptions.ValidationError` при переборе.

- [ ] **Step 1: Написать падающий тест**

Добавить в `TestOverpaymentGuard`:

```python
    # ── Task 3: guard ────────────────────────────────
    def test_exact_remaining_closure_ok(self):
        plan = _plan(remaining_balance=165.91)
        _pt("Installment Plan", plan.name, 165.91)._guard_installment_overpayment()  # без исключения

    def test_under_remaining_ok(self):
        plan = _plan(remaining_balance=165.91)
        _pt("Installment Plan", plan.name, 55)._guard_installment_overpayment()

    def test_tolerance_rounding_ok(self):
        plan = _plan(remaining_balance=165.91)
        _pt("Installment Plan", plan.name, 165.915)._guard_installment_overpayment()

    def test_gross_overpayment_blocked_and_message_has_remaining(self):
        plan = _plan(remaining_balance=165.91)
        with self.assertRaises(frappe.exceptions.ValidationError) as cm:
            _pt("Installment Plan", plan.name, 660000)._guard_installment_overpayment()
        self.assertIn("165.91", str(cm.exception))

    def test_sales_order_reference_guarded(self):
        so = _db_insert("Sales Order", total_amount=1200, order_date="2026-01-01", docstatus=1)
        _plan(remaining_balance=100, sales_order=so.name)
        with self.assertRaises(frappe.exceptions.ValidationError):
            _pt("Sales Order", so.name, 200)._guard_installment_overpayment()

    def test_closed_plan_blocks_any_payment(self):
        plan = _plan(remaining_balance=0)   # remaining 0, строк графика нет
        with self.assertRaises(frappe.exceptions.ValidationError):
            _pt("Installment Plan", plan.name, 10)._guard_installment_overpayment()

    def test_header_zero_uses_schedule_due_for_guard(self):
        plan = _plan(remaining_balance=0)
        _sched_row(plan.name, 1, amount=100, paid_amount=60)   # due = 40
        _pt("Installment Plan", plan.name, 40)._guard_installment_overpayment()   # ok
        with self.assertRaises(frappe.exceptions.ValidationError):
            _pt("Installment Plan", plan.name, 60)._guard_installment_overpayment()

    def test_migrate_flag_bypasses_guard(self):
        plan = _plan(remaining_balance=165.91)
        frappe.flags.in_migrate = True
        try:
            _pt("Installment Plan", plan.name, 660000)._guard_installment_overpayment()  # без исключения
        finally:
            frappe.flags.in_migrate = False
```

- [ ] **Step 2: Запустить — убедиться, что падает (AttributeError)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_overpayment_guard`
Expected: FAIL — `AttributeError: 'PaymentTransaction' object has no attribute '_guard_installment_overpayment'`.

- [ ] **Step 3: Реализовать константу, метод и подключение**

Добавить модульную константу рядом с другими константами вверху файла:

```python
_OVERPAYMENT_TOLERANCE = 0.01
```

Добавить метод в класс `PaymentTransaction` (после `_validate_payment_date`):

```python
    def _guard_installment_overpayment(self):
        """Блокирует платёж, превышающий остаток связанного плана (сверх копейки), ДО
        проведения. Ловит ошибки суммы/валюты (инцидент INST-2026-00065: 660000 сум,
        введённые в поле USD)."""
        if (frappe.flags.in_migrate or frappe.flags.in_import
                or frappe.flags.in_patch or frappe.flags.in_install):
            return
        plan_name = _resolve_installment_plan_name(self)
        if not plan_name:
            return
        amt = flt(self.amount)
        if amt <= 0:
            return
        remaining = _installment_plan_remaining(plan_name)
        if amt > remaining + _OVERPAYMENT_TOLERANCE:
            frappe.throw(
                _(
                    "Сумма платежа {0} превышает остаток по договору {1}. "
                    "Остаток: {2} USD. Проверьте сумму — возможно, введена в сумах "
                    "вместо USD. Для полного закрытия введите {2}."
                ).format(f"{amt:.2f}", plan_name, f"{remaining:.2f}")
            )
```

Подключить в `validate` (последним вызовом):

```python
    def validate(self):
        self.autolink_single_open_installment_plan()
        self.apply_payment_totals()
        self._validate_payment_date()
        self._guard_installment_overpayment()
```

- [ ] **Step 4: Запустить — убедиться, что проходит весь модуль**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_overpayment_guard`
Expected: PASS (14 тестов).

- [ ] **Step 5: Регрессия — прогнать соседние тесты платежей/движка**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_cost_recovery_engine`
Expected: PASS (без регрессий — аллокация ведёт себя как раньше).

- [ ] **Step 6: Коммит**

```bash
git add nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py nasiya365/tests/test_overpayment_guard.py
git commit -m "feat(payment): блок переплаты по рассрочке в validate() с показом остатка"
```

---

## Проверка на dev (после Task 3, вручную — опционально)

1. Перезапустить web-воркер, чтобы подхватил новый контроллер: `docker compose restart backend` (ждать готовности).
2. В UI открыть договор с остатком, попробовать провести платёж больше остатка → должно блокировать с сообщением, показывающим точный остаток.
3. Провести платёж ровно на остаток → проходит, договор закрывается.

## После завершения

- Ветка `feat/overpayment-guard` (спека уже закоммичена там же).
- Мерж в `main` + пуш — **только по явному разрешению пользователя**; деплой на Frappe Cloud делает пользователь сам.
