# C1 — филиал у рассрочки — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать Installment Plan собственное поле `branch` с автозаполнением при создании, и научить отчёты фильтровать по нему напрямую (с fallback на старый путь через `sales_order`), чтобы новые рассрочки перестали пропадать под фильтром филиала.

**Architecture:** Новое Link-поле `branch` на Installment Plan. Автозаполнение в `validate()` по цепочке: заказ → склад → единственный филиал оператора; при неоднозначности (несколько филиалов) — обязательный ручной выбор. Отчёты меняют резолв филиала с `ip.sales_order → SO.branch` на «прямое `ip.branch` ИЛИ (пусто И старый путь через SO)». Старые договоры не бэкфиллятся — их обслуживает fallback.

**Tech Stack:** Frappe v16, Python, MariaDB. Тесты — `unittest.TestCase` + SAVEPOINT (паттерн `nasiya365/tests/test_cost_recovery_engine.py`), запуск `bench run-tests`.

## Global Constraints

- Старые договоры НЕ трогаем (нет бэкфилла). Совместимость только через SQL-fallback.
- Автозаполнение НЕ перезаписывает вручную выбранный `branch`.
- Неоднозначность (оператор с >1 филиалом, источник не найден) → `frappe.throw("Выберите филиал для рассрочки.")`. Оператор с 0 филиалов или unrestricted-админ → оставить пустым, НЕ бросать.
- Fallback-паттерн филиала плана (verbatim, `:branches` = список / `%s`):
  `(ip.branch IN (...) OR ((ip.branch IS NULL OR ip.branch = '') AND ip.sales_order IN (SELECT name FROM \`tabSales Order\` WHERE branch IN (...))))`
- Branch child-table на Branch: parenttype `Branch`, parentfield `branch_users`, doctype `Branch User` (поля user, role, is_active).
- Unrestricted-роли: `System Manager`, `Nasiya365 Admin` (`_is_unrestricted`).
- Поле `branch`: `fieldtype=Link, options=Branch, label=Филиал, in_standard_filter=1`.
- Запуск тестов: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module <mod>`.
- Суммы/график/прибыль/распределение НЕ меняются — только видимость под фильтром филиала.

---

### Task 1: Поле `branch` + автозаполнение

**Files:**
- Modify: `nasiya365/nasiya365/doctype/installment_plan/installment_plan.json` (добавить поле `branch`)
- Modify: `nasiya365/nasiya365/doctype/installment_plan/installment_plan.py` (модульные `_plan_branch_from_sources`, `_branch_decision_for_user`; метод `_autoset_branch`; вызов в `validate()`)
- Create: `nasiya365/tests/test_plan_branch.py`

**Interfaces:**
- Produces: `_plan_branch_from_sources(sales_order, stock_entry) -> str | None`
- Produces: `_branch_decision_for_user(user) -> tuple[str, str | None]` — `("ok", branch)` | `("ambiguous", None)` | `("skip", None)`
- Produces: `InstallmentPlan._autoset_branch(self)` — ставит `self.branch` или `frappe.throw`
- Produces (тест-хелперы): `_db_insert`, `_seed_branch`, `_seed_branch_user(branch, user, active=1)`, `_seed_warehouse(branch)`, `_seed_stock_entry(warehouse)`, `_seed_sales_order(branch)`

- [ ] **Step 1: Добавить поле `branch` в installment_plan.json**

Найти поле `sales_order` в `installment_plan.json` и сразу ПОСЛЕ его закрывающей `},` вставить:

```json
  {
   "fieldname": "branch",
   "fieldtype": "Link",
   "options": "Branch",
   "label": "Филиал",
   "in_standard_filter": 1
  },
```

- [ ] **Step 2: Применить схему на dev (колонка в БД)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz migrate`
Expected: завершается без ошибок; колонка `branch` появляется в `tabInstallment Plan`.
(Проверка: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz execute frappe.db.sql --kwargs '{"query":"SHOW COLUMNS FROM \`tabInstallment Plan\` LIKE \"branch\""}'` — одна строка.)

- [ ] **Step 3: Написать падающий тест (создать файл)**

```python
# nasiya365/tests/test_plan_branch.py
import unittest
import frappe

from nasiya365.nasiya365.doctype.installment_plan.installment_plan import (
    _plan_branch_from_sources,
    _branch_decision_for_user,
)


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _seed_branch(city="Test"):
    return _db_insert("Branch", branch_name=frappe.generate_hash(length=6), city=city)


def _seed_branch_user(branch_name, user, active=1):
    return _db_insert(
        "Branch User", parent=branch_name, parenttype="Branch",
        parentfield="branch_users", idx=1, user=user, is_active=active,
    )


def _seed_warehouse(branch_name):
    return _db_insert("Warehouse", warehouse_name=frappe.generate_hash(length=6), branch=branch_name)


def _seed_stock_entry(warehouse_name):
    return _db_insert("Stock Entry", entry_type="Поступление",
                      posting_date="2026-01-01", warehouse=warehouse_name)


def _seed_sales_order(branch_name):
    return _db_insert("Sales Order", total_amount=1000, order_date="2026-01-01",
                      branch=branch_name, docstatus=1)


def _plan(**fields):
    """In-memory Installment Plan (не сохраняется) для прямого вызова _autoset_branch."""
    return frappe.get_doc({"doctype": "Installment Plan", **fields})


class TestPlanBranchAutoset(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("plan_branch_test")

    def tearDown(self):
        frappe.db.rollback(save_point="plan_branch_test")

    # ── _plan_branch_from_sources ────────────────────
    def test_source_from_sales_order(self):
        b = _seed_branch()
        so = _seed_sales_order(b.name)
        self.assertEqual(_plan_branch_from_sources(so.name, None), b.name)

    def test_source_from_stock_entry_warehouse(self):
        b = _seed_branch()
        wh = _seed_warehouse(b.name)
        se = _seed_stock_entry(wh.name)
        self.assertEqual(_plan_branch_from_sources(None, se.name), b.name)

    def test_source_sales_order_wins_over_stock_entry(self):
        b1 = _seed_branch(); b2 = _seed_branch()
        so = _seed_sales_order(b1.name)
        wh = _seed_warehouse(b2.name); se = _seed_stock_entry(wh.name)
        self.assertEqual(_plan_branch_from_sources(so.name, se.name), b1.name)

    def test_source_none_when_no_refs(self):
        self.assertIsNone(_plan_branch_from_sources(None, None))

    # ── _branch_decision_for_user (явный user, без set_user) ──
    def test_decision_single_branch_ok(self):
        b = _seed_branch()
        user = "op_single_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(b.name, user)
        self.assertEqual(_branch_decision_for_user(user), ("ok", b.name))

    def test_decision_multi_branch_ambiguous(self):
        b1 = _seed_branch(); b2 = _seed_branch()
        user = "op_multi_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(b1.name, user)
        _seed_branch_user(b2.name, user)
        self.assertEqual(_branch_decision_for_user(user), ("ambiguous", None))

    def test_decision_no_branch_skips(self):
        user = "op_none_%s@test.local" % frappe.generate_hash(length=4)
        self.assertEqual(_branch_decision_for_user(user), ("skip", None))

    def test_decision_unrestricted_skips(self):
        self.assertEqual(_branch_decision_for_user("Administrator"), ("skip", None))

    # ── _autoset_branch (оркестрация) ────────────────
    def test_autoset_keeps_manual_branch(self):
        b1 = _seed_branch(); b2 = _seed_branch()
        so = _seed_sales_order(b2.name)
        p = _plan(branch=b1.name, sales_order=so.name)
        p._autoset_branch()
        self.assertEqual(p.branch, b1.name)   # ручной не перезаписан

    def test_autoset_from_sales_order(self):
        b = _seed_branch()
        so = _seed_sales_order(b.name)
        p = _plan(sales_order=so.name)
        p._autoset_branch()
        self.assertEqual(p.branch, b.name)

    def test_autoset_ambiguous_throws(self):
        # decision-функцию мокаем (session.user в тестах = Administrator/unrestricted,
        # поэтому throw-ветку изолируем от привязок пользователя)
        import nasiya365.nasiya365.doctype.installment_plan.installment_plan as ip_mod
        orig = ip_mod._branch_decision_for_user
        ip_mod._branch_decision_for_user = lambda u: ("ambiguous", None)
        try:
            p = _plan()   # без sales_order/stock_entry/branch
            with self.assertRaises(frappe.exceptions.ValidationError):
                p._autoset_branch()
        finally:
            ip_mod._branch_decision_for_user = orig

    def test_autoset_skip_leaves_empty(self):
        import nasiya365.nasiya365.doctype.installment_plan.installment_plan as ip_mod
        orig = ip_mod._branch_decision_for_user
        ip_mod._branch_decision_for_user = lambda u: ("skip", None)
        try:
            p = _plan()
            p._autoset_branch()
            self.assertFalse(p.get("branch"))
        finally:
            ip_mod._branch_decision_for_user = orig
```

- [ ] **Step 4: Запустить — убедиться, что падает (ImportError)**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_plan_branch`
Expected: FAIL — `ImportError: cannot import name '_plan_branch_from_sources'`.

- [ ] **Step 5: Реализовать резолверы + метод + вызов**

В `installment_plan.py` добавить модульные функции (рядом с другими module-level помощниками, вне класса). `flt` уже импортирован; `frappe` и `_` тоже:

```python
def _plan_branch_from_sources(sales_order=None, stock_entry=None):
    """Филиал плана из его собственных ссылок: заказ → склад. None если не определить."""
    if sales_order:
        b = frappe.db.get_value("Sales Order", sales_order, "branch")
        if b:
            return b
    if stock_entry:
        wh = frappe.db.get_value("Stock Entry", stock_entry, "warehouse")
        if wh:
            b = frappe.db.get_value("Warehouse", wh, "branch")
            if b:
                return b
    return None


def _branch_decision_for_user(user):
    """('ok', branch) — ровно один филиал; ('ambiguous', None) — несколько;
    ('skip', None) — unrestricted-админ или оператор без филиалов (не блокируем)."""
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    if _is_unrestricted(user):
        return ("skip", None)
    branches = _get_user_branches(user)
    if len(branches) == 1:
        return ("ok", branches[0])
    if not branches:
        return ("skip", None)
    return ("ambiguous", None)
```

Добавить метод в класс `InstallmentPlan`:

```python
    def _autoset_branch(self):
        """Заполнить `branch`, если не задан вручную: заказ → склад → единственный
        филиал оператора; при неоднозначности требовать явный выбор."""
        if self.get("branch"):
            return
        b = _plan_branch_from_sources(self.get("sales_order"), self.get("stock_entry"))
        if b:
            self.branch = b
            return
        decision, br = _branch_decision_for_user(frappe.session.user)
        if decision == "ok":
            self.branch = br
        elif decision == "ambiguous":
            frappe.throw(_("Выберите филиал для рассрочки."))
        # skip → оставить пустым
```

Подключить в `validate()` — в основном пути, сразу после строки `self.frequency = _normalize_frequency(self.frequency)` (та, что идёт перед `self.validate_unique_sales_order()`):

```python
        self.frequency = _normalize_frequency(self.frequency)

        self._autoset_branch()

        self.validate_unique_sales_order()
```

- [ ] **Step 6: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_plan_branch`
Expected: PASS (13 тестов).

- [ ] **Step 7: Коммит**

```bash
git add nasiya365/nasiya365/doctype/installment_plan/installment_plan.json nasiya365/nasiya365/doctype/installment_plan/installment_plan.py nasiya365/tests/test_plan_branch.py
git commit -m "feat(installment): поле Филиал + автозаполнение при создании"
```

---

### Task 2: Fallback-резолв видимости (права + explicit-фильтры)

**Files:**
- Modify: `nasiya365/api/bnpl_dashboard.py` (`_user_branch_clause`: прямое поле ИЛИ старый путь; + optional `user` param для тестируемости)
- Modify: `nasiya365/api/profit.py` (`_compute_accrual` explicit branch filter)
- Modify: `nasiya365/nasiya365/report/collections_and_overdue/collections_and_overdue.py` (explicit filter + SELECT branch)
- Modify: `nasiya365/tests/test_plan_branch.py` (тесты видимости)

**Interfaces:**
- Consumes: поле `branch` (Task 1), тест-хелперы `_db_insert`, `_seed_branch`, `_seed_branch_user`, `_seed_sales_order`.
- Changes: `_user_branch_clause(plan_alias="ip", user=None)` — новый необязательный `user` (default `frappe.session.user`), поведение при `user=None` не меняется.

- [ ] **Step 1: Написать падающий тест**

Добавить в `test_plan_branch.py` новый класс (и helper для сид-плана с колонкой branch):

```python
def _seed_plan(branch=None, sales_order=None):
    return _db_insert(
        "Installment Plan", imei="IMEI" + frappe.generate_hash(length=6),
        principal_amount=1000, financed_amount=700, total_interest=200,
        total_amount=1200, start_date="2026-01-01",
        status="Активный", contract_status="Активный", docstatus=1,
        branch=branch, sales_order=sales_order,
    )


class TestPlanBranchVisibility(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("plan_branch_vis")

    def tearDown(self):
        frappe.db.rollback(save_point="plan_branch_vis")

    def _visible(self, plan_name, user):
        from nasiya365.api.bnpl_dashboard import _user_branch_clause
        frappe.cache().delete_value("nasiya365:user_branches:%s" % user)
        frag, params = _user_branch_clause("ip", user=user)
        rows = frappe.db.sql(
            f"SELECT ip.name FROM `tabInstallment Plan` ip WHERE ip.name = %s {frag}",
            (plan_name, *params),
        )
        frappe.cache().delete_value("nasiya365:user_branches:%s" % user)
        return bool(rows)

    def test_new_plan_direct_branch_visible_to_its_branch(self):
        a = _seed_branch()
        user = "op_a_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(a.name, user)
        p = _seed_plan(branch=a.name, sales_order=None)   # новый: своё поле, без SO
        self.assertTrue(self._visible(p.name, user))

    def test_new_plan_not_visible_to_other_branch(self):
        a = _seed_branch(); other = _seed_branch()
        user_other = "op_o_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(other.name, user_other)
        p = _seed_plan(branch=a.name, sales_order=None)
        self.assertFalse(self._visible(p.name, user_other))

    def test_legacy_plan_via_sales_order_still_visible(self):
        a = _seed_branch()
        user = "op_a2_%s@test.local" % frappe.generate_hash(length=4)
        _seed_branch_user(a.name, user)
        so = _seed_sales_order(a.name)
        p = _seed_plan(branch=None, sales_order=so.name)   # старый: branch пуст, SO→A
        self.assertTrue(self._visible(p.name, user))       # fallback работает
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_plan_branch`
Expected: FAIL — `_user_branch_clause() got an unexpected keyword argument 'user'` (или тест видимости нового плана падает, т.к. clause ещё только через sales_order).

- [ ] **Step 3: Реализовать fallback в `_user_branch_clause`**

Заменить тело `_user_branch_clause` (`bnpl_dashboard.py`):

```python
def _user_branch_clause(plan_alias: str = "ip", user: str = None):
    """
    Return (sql_fragment, params) restricting Installment-Plan-backed queries
    to the caller's assigned branches. Fragment begins with ' AND ' when non-empty.
    Для unrestricted-пользователей возвращает ('', ()).
    Для пользователей без филиалов — no-match фрагмент (видит ничего).

    Филиал плана берётся из собственного поля `branch`; при пустом поле —
    fallback на старый путь через `sales_order → Sales Order.branch` (старые
    договоры без своего branch не пропадают).
    """
    from nasiya365.permissions import _get_user_branches, _is_unrestricted

    if user is None:
        user = frappe.session.user
    if _is_unrestricted(user):
        return ("", ())
    branches = _get_user_branches(user)
    if not branches:
        return (" AND 1=0", ())
    placeholders = ",".join(["%s"] * len(branches))
    fragment = (
        f" AND ({plan_alias}.branch IN ({placeholders})"
        f" OR (({plan_alias}.branch IS NULL OR {plan_alias}.branch = '')"
        f" AND {plan_alias}.sales_order IN "
        f"(SELECT name FROM `tabSales Order` WHERE branch IN ({placeholders}))))"
    )
    return (fragment, tuple(branches) + tuple(branches))
```

(Параметры дублируются: список филиалов подставляется дважды — для прямого поля и для подзапроса.)

- [ ] **Step 4: Реализовать fallback в explicit-фильтрах**

В `profit.py` `_compute_accrual` заменить:

```python
    expl = " AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch = %s)" if branch else ""
    expl_params = [branch] if branch else []
```

на:

```python
    expl = (
        " AND (ip.branch = %s OR ((ip.branch IS NULL OR ip.branch = '')"
        " AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch = %s)))"
    ) if branch else ""
    expl_params = [branch, branch] if branch else []
```

В `collections_and_overdue.py` заменить тот же `expl`/`expl_params` блок на идентичный вариант выше. И заменить SELECT-колонку branch:

```python
               (SELECT branch FROM `tabSales Order` so WHERE so.name = ip.sales_order) AS branch,
```

на:

```python
               COALESCE(ip.branch,
                        (SELECT branch FROM `tabSales Order` so WHERE so.name = ip.sales_order)) AS branch,
```

- [ ] **Step 5: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_plan_branch`
Expected: PASS (16 тестов: 13 из Task 1 + 3 видимости).

- [ ] **Step 6: Регресс движка**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_cost_recovery_engine`
Expected: PASS (4/4 — суммы под branch-фильтром не изменились для планов через SO).

- [ ] **Step 7: Коммит**

```bash
git add nasiya365/api/bnpl_dashboard.py nasiya365/api/profit.py nasiya365/nasiya365/report/collections_and_overdue/collections_and_overdue.py nasiya365/tests/test_plan_branch.py
git commit -m "fix(reports): фильтр филиала читает ip.branch с fallback на sales_order"
```

---

### Task 3: Fallback в резолве филиала платежа (суммы P&L)

**Files:**
- Modify: `nasiya365/api/profit.py` (`_PAYMENT_BRANCH_CASE`)
- Modify: `nasiya365/tests/test_plan_branch.py` (тест суммирования по филиалу)

**Interfaces:**
- Consumes: поле `branch`, `_seed_plan`, `_seed_branch`, `_db_insert`.
- Changes: `_PAYMENT_BRANCH_CASE` — для Installment Plan reference сперва `ip.branch`, при пустом → `so.branch` через `ip.sales_order`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `test_plan_branch.py` (класс `TestPlanBranchVisibility` или новый):

```python
    def test_payment_branch_uses_direct_field(self):
        # платёж по новому плану (branch=A, без SO) должен относиться к филиалу A
        a = _seed_branch()
        p = _seed_plan(branch=a.name, sales_order=None)
        _db_insert("Payment Transaction", reference_doctype="Installment Plan",
                   reference_name=p.name, amount=100, status="Завершен",
                   payment_date="2026-06-01", docstatus=1)
        from nasiya365.api.profit import _PAYMENT_BRANCH_CASE
        row = frappe.db.sql(
            f"SELECT ({_PAYMENT_BRANCH_CASE}) AS branch FROM `tabPayment Transaction` pt "
            f"WHERE pt.reference_name = %s AND pt.reference_doctype = 'Installment Plan'",
            (p.name,),
        )
        self.assertEqual(row[0][0], a.name)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_plan_branch`
Expected: FAIL — резолв возвращает `NULL` (план без sales_order, старый CASE не видит ip.branch).

- [ ] **Step 3: Реализовать fallback в `_PAYMENT_BRANCH_CASE`**

Заменить в `profit.py`:

```python
_PAYMENT_BRANCH_CASE = """CASE
                 WHEN pt.reference_doctype = 'Installment Plan' THEN (
                     SELECT so.branch FROM `tabSales Order` so
                     JOIN `tabInstallment Plan` ip ON ip.sales_order = so.name
                     WHERE ip.name = pt.reference_name LIMIT 1)
                 WHEN pt.reference_doctype = 'Sales Order' THEN (
                     SELECT branch FROM `tabSales Order` WHERE name = pt.reference_name LIMIT 1)
               END"""
```

на:

```python
_PAYMENT_BRANCH_CASE = """CASE
                 WHEN pt.reference_doctype = 'Installment Plan' THEN (
                     SELECT COALESCE(ip.branch, (
                         SELECT so.branch FROM `tabSales Order` so
                         WHERE so.name = ip.sales_order LIMIT 1))
                     FROM `tabInstallment Plan` ip
                     WHERE ip.name = pt.reference_name LIMIT 1)
                 WHEN pt.reference_doctype = 'Sales Order' THEN (
                     SELECT branch FROM `tabSales Order` WHERE name = pt.reference_name LIMIT 1)
               END"""
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_plan_branch`
Expected: PASS (17 тестов).

- [ ] **Step 5: Регресс движка**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_cost_recovery_engine`
Expected: PASS (4/4 — планы через SO по-прежнему резолвятся, суммы не изменились).

- [ ] **Step 6: Коммит**

```bash
git add nasiya365/api/profit.py nasiya365/tests/test_plan_branch.py
git commit -m "fix(pnl): резолв филиала платежа читает ip.branch с fallback на SO"
```

---

## Проверка на dev (после Task 3, вручную)

1. `docker compose restart backend` (ждать готовности).
2. Создать рассрочку под кассиром одного филиала → поле «Филиал» заполнилось само.
3. Отчёт «Прибыль и поступления» с фильтром этого филиала → новый договор виден.
4. Отчёт «Сборы и просрочка» с фильтром филиала → новый договор в списке.

## После завершения

- Ветка `fix/c1-plan-branch` (спека уже там).
- Мерж в `main` + пуш — только по явному разрешению; деплой на Frappe Cloud (migrate добавит колонку) делает пользователь сам.
