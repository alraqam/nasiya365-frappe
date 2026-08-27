# История оплат под графиком — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Показать на форме договора под графиком read-only таблицу «История оплат» — все фактические платежи по датам, из существующих Payment Transaction (без нового хранилища).

**Architecture:** Whitelisted backend-метод `get_payment_history(installment_plan)` отдаёт проведённые платежи (дата, сумма, метод, №). HTML-поле `payment_history_html` на форме — контейнер; client JS в `refresh` вызывает метод и рендерит таблицу. Данные читаются вживую; в БД ничего нового не хранится.

**Tech Stack:** Frappe v16, Python, MariaDB, client JS (frappe.ui.form). Тесты — `unittest.TestCase` + SAVEPOINT (паттерн `test_cost_recovery_engine.py`), запуск `bench run-tests`.

## Global Constraints

- Только чтение: график, суммы, аллокация, прибыль — НЕ трогаем.
- Показываем только проведённые платежи: `docstatus=1 AND status='Завершен'`.
- Сортировка по дате: `ORDER BY payment_date ASC, creation ASC`.
- Метод: distinct `payment_method` из `payment_lines` (parenttype 'Payment Transaction', parentfield 'payment_lines') — 1 → он; >1 → `"Комбинированный"`; 0 строк → header `payment_method` или `"—"`.
- Сумма платежа — header `amount` (USD-итог).
- Возврат `get_payment_history`: dict `{"payments": [{name, payment_date, amount, method}], "total": float, "count": int}`.
- HTML-поле `payment_history_html` (fieldtype HTML) хранилищем НЕ является.
- Запуск тестов: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module <mod>`.

---

### Task 1: Backend `get_payment_history` + тест

**Files:**
- Modify: `nasiya365/nasiya365/doctype/installment_plan/installment_plan.py` (whitelisted `get_payment_history`)
- Create: `nasiya365/tests/test_payment_history.py`

**Interfaces:**
- Produces: `get_payment_history(installment_plan) -> dict` (`{"payments": [...], "total": float, "count": int}`)
- Produces (тест-хелперы): `_db_insert`, `_seed_plan()`, `_seed_payment(plan, amount, date, methods, docstatus=1, status="Завершен")`

- [ ] **Step 1: Написать падающий тест (создать файл)**

```python
# nasiya365/tests/test_payment_history.py
import unittest
import frappe

from nasiya365.nasiya365.doctype.installment_plan.installment_plan import (
    get_payment_history,
)


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_plan():
    return _db_insert(
        "Installment Plan", imei="PHIST" + frappe.generate_hash(length=5),
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2026-01-01",
        status="Активный", contract_status="Активный", docstatus=1,
    )


def _seed_payment(plan_name, amount, date, methods, docstatus=1, status="Завершен"):
    pt = _db_insert(
        "Payment Transaction", reference_doctype="Installment Plan",
        reference_name=plan_name, amount=amount, payment_date=date,
        status=status, docstatus=docstatus,
    )
    per = amount / len(methods) if methods else 0
    for i, m in enumerate(methods):
        _db_insert(
            "Payment Transaction Line", parent=pt.name,
            parenttype="Payment Transaction", parentfield="payment_lines",
            idx=i + 1, payment_method=m, amount=per,
        )
    return pt


class TestPaymentHistory(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("pay_hist_test")

    def tearDown(self):
        frappe.db.rollback(save_point="pay_hist_test")

    def test_three_payments_sorted_and_totalled(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-20", ["Карта"])
        _seed_payment(plan.name, 60, "2026-09-10", ["Наличные USD"])
        _seed_payment(plan.name, 80, "2026-09-28", ["Карта"])
        r = get_payment_history(plan.name)
        self.assertEqual(r["count"], 3)
        self.assertEqual([p["payment_date"] for p in r["payments"]],
                         ["2026-09-10", "2026-09-20", "2026-09-28"])  # sorted asc
        self.assertAlmostEqual(r["total"], 240.0, places=2)

    def test_single_method_shown(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-01", ["Карта"])
        r = get_payment_history(plan.name)
        self.assertEqual(r["payments"][0]["method"], "Карта")

    def test_combined_method(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-01", ["Карта", "Наличные USD"])
        r = get_payment_history(plan.name)
        self.assertEqual(r["payments"][0]["method"], "Комбинированный")

    def test_cancelled_and_draft_excluded(self):
        plan = _seed_plan()
        _seed_payment(plan.name, 100, "2026-09-01", ["Карта"])                 # ok
        _seed_payment(plan.name, 50, "2026-09-02", ["Карта"], docstatus=2)     # cancelled
        _seed_payment(plan.name, 50, "2026-09-03", ["Карта"], docstatus=0,
                      status="Ожидает")                                        # draft
        r = get_payment_history(plan.name)
        self.assertEqual(r["count"], 1)
        self.assertAlmostEqual(r["total"], 100.0, places=2)

    def test_empty_plan(self):
        plan = _seed_plan()
        r = get_payment_history(plan.name)
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["payments"], [])
        self.assertEqual(r["total"], 0)
```

- [ ] **Step 2: Запустить — убедиться, что падает (ImportError)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_payment_history`
Expected: FAIL — `ImportError: cannot import name 'get_payment_history'`.

- [ ] **Step 3: Реализовать метод**

Добавить в конец `installment_plan.py` (module-level, рядом с другими `@frappe.whitelist()`):

```python
@frappe.whitelist()
def get_payment_history(installment_plan):
    """Проведённые платежи по договору для read-only выписки под графиком.

    Читает существующие Payment Transaction (docstatus=1, Завершен). Ничего не хранит.
    """
    rows = frappe.db.sql(
        """
        SELECT name, payment_date, amount, payment_method
        FROM `tabPayment Transaction`
        WHERE reference_doctype = 'Installment Plan'
          AND reference_name = %s
          AND docstatus = 1
          AND status = 'Завершен'
        ORDER BY payment_date ASC, creation ASC
        """,
        (installment_plan,), as_dict=True,
    )
    payments = []
    total = 0.0
    for r in rows:
        methods = frappe.db.sql(
            """SELECT DISTINCT payment_method FROM `tabPayment Transaction Line`
               WHERE parent = %s AND parenttype = 'Payment Transaction'
                 AND IFNULL(payment_method, '') != ''""",
            (r.name,), pluck=True,
        )
        if len(methods) == 1:
            method = methods[0]
        elif len(methods) > 1:
            method = "Комбинированный"
        else:
            method = r.payment_method or "—"
        total += flt(r.amount)
        payments.append({
            "name": r.name,
            "payment_date": str(r.payment_date) if r.payment_date else None,
            "amount": flt(r.amount),
            "method": method,
        })
    return {"payments": payments, "total": total, "count": len(payments)}
```

(`flt` уже импортирован в файле.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_payment_history`
Expected: PASS (5 тестов).

- [ ] **Step 5: Коммит**

```bash
git add nasiya365/nasiya365/doctype/installment_plan/installment_plan.py nasiya365/tests/test_payment_history.py
git commit -m "feat(installment): backend get_payment_history для выписки под графиком"
```

---

### Task 2: HTML-поле на форме + client JS рендер

**Files:**
- Modify: `nasiya365/nasiya365/doctype/installment_plan/installment_plan.json` (поле `payment_history_html` + field_order)
- Modify: `nasiya365/nasiya365/doctype/installment_plan/installment_plan.js` (рендер в `refresh`)

**Interfaces:**
- Consumes: `get_payment_history` (Task 1).

- [ ] **Step 1: Добавить HTML-поле в installment_plan.json**

В массив `fields` добавить (например после поля `schedule`):

```json
  {
   "fieldname": "payment_history_html",
   "fieldtype": "HTML",
   "label": "История оплат"
  },
```

В массив `field_order` добавить строку `"payment_history_html"` сразу ПОСЛЕ `"schedule"`.

- [ ] **Step 2: Применить схему на dev**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz migrate`
Expected: без ошибок (HTML-поле не создаёт колонку, но синхронизирует doctype).

- [ ] **Step 3: Добавить рендер в installment_plan.js**

В существующем `refresh(frm)` — ПОСЛЕ блока `load_payment_meta(...)` добавить вызов:

```javascript
        render_payment_history(frm);
```

И добавить функцию (module-level в том же файле):

```javascript
/** Read-only выписка «История оплат» под графиком — из проведённых платежей. */
function render_payment_history(frm) {
    const field = frm.get_field('payment_history_html');
    if (!field || !frm.doc.name || frm.is_new()) return;

    frappe.call({
        method: 'nasiya365.nasiya365.doctype.installment_plan.installment_plan.get_payment_history',
        args: { installment_plan: frm.doc.name },
        callback: function (r) {
            const data = r.message || { payments: [], total: 0, count: 0 };
            field.$wrapper.html(build_payment_history_html(data));
        },
    });
}

function build_payment_history_html(data) {
    const esc = frappe.utils.escape_html;
    if (!data.count) {
        return `<div class="text-muted" style="padding:8px 0;">${__('Оплат пока нет')}</div>`;
    }
    const rows = data.payments.map(function (p) {
        return `<tr>
            <td>${esc(frappe.datetime.str_to_user(p.payment_date) || '')}</td>
            <td style="text-align:right;">${esc(format_currency(flt(p.amount), 'USD'))}</td>
            <td>${esc(p.method || '—')}</td>
            <td class="text-muted">${esc(p.name)}</td>
        </tr>`;
    }).join('');
    return `
        <table class="table table-bordered" style="margin-bottom:0;">
            <thead><tr>
                <th>${__('Дата')}</th>
                <th style="text-align:right;">${__('Сумма')}</th>
                <th>${__('Метод')}</th>
                <th>${__('№ платежа')}</th>
            </tr></thead>
            <tbody>${rows}</tbody>
            <tfoot><tr>
                <th>${__('Всего оплачено')}</th>
                <th style="text-align:right;">${esc(format_currency(flt(data.total), 'USD'))}</th>
                <th colspan="2" class="text-muted">${data.count} ${__('платежей')}</th>
            </tr></tfoot>
        </table>`;
}
```

- [ ] **Step 4: Проверка синтаксиса**

Run: `node --check nasiya365/nasiya365/doctype/installment_plan/installment_plan.js && python3 -c "import json; json.load(open('nasiya365/nasiya365/doctype/installment_plan/installment_plan.json'))"`
Expected: без ошибок.

- [ ] **Step 5: Коммит**

```bash
git add nasiya365/nasiya365/doctype/installment_plan/installment_plan.json nasiya365/nasiya365/doctype/installment_plan/installment_plan.js
git commit -m "feat(installment): таблица История оплат под графиком на форме"
```

---

## Проверка на dev (после Task 2, вручную)

1. `docker compose restart backend` (ждать готовности) — чтобы web подхватил backend-метод.
2. Открыть договор с несколькими платежами → под графиком видна таблица «История оплат»
   (дата/сумма/метод/№ + итог), отсортирована по дате.
3. Договор без платежей → «Оплат пока нет».

## После завершения

- Ветка `feat/payment-history` (спека + макет уже там).
- Мерж в `main` + пуш — только по явному разрешению; деплой (migrate синхронизирует поле) делает пользователь сам.
