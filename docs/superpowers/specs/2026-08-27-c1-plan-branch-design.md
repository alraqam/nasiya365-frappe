# C1 — филиал у рассрочки (branch filter drops installment revenue) — дизайн

**Дата:** 2026-08-27
**Область:** Installment Plan + отчёты (P&L, Collections & Overdue) + права по филиалу
**Тип:** исправление бага (отчёты не сходятся под фильтром филиала) + новое поле

---

## Проблема (root cause, подтверждён на текущем коде и данных)

`Installment Plan` **не имеет собственного поля `branch`**. Филиал плана резолвится
**только** через `plan.sales_order → Sales Order.branch`:
- `_user_branch_clause` (`nasiya365/api/bnpl_dashboard.py:19-37`) — права + Collections;
- `_PAYMENT_BRANCH_CASE`, `_branch_clause_for`, `_compute_accrual` (`nasiya365/api/profit.py`);
- `collections_and_overdue.py:33-43`.

`_user_branch_clause` строит:
`AND ip.sales_order IN (SELECT name FROM tabSales Order WHERE branch IN (...))`.
План с пустым `sales_order` не проходит это условие → **исчезает** при фильтре по филиалу
и для branch-ограниченного (не-админ) пользователя.

**Масштаб (dev, замер 2026-08-27):** 19 из 20 планов имеют пустой `sales_order` → ~95%
рассрочек пропадает под branch-фильтром. Это и есть жалоба «отчёты не сходятся с реальностью».

## Решение (согласовано)

Дать плану собственное поле `branch`, автозаполнять его при создании, и научить отчёты
читать это поле напрямую (с fallback на старый путь через `sales_order` для совместимости).
**Старые договоры НЕ трогаем** — бэкфилла нет; для них сохраняется текущий путь через
`sales_order` (поведение не ухудшается).

### Секция 1 — поле `branch` + автозаполнение

Новое поле в `installment_plan.json` (паттерн скопирован с Sales Order):

```json
{
  "fieldname": "branch",
  "fieldtype": "Link",
  "options": "Branch",
  "label": "Филиал",
  "in_standard_filter": 1
}
```

Автозаполнение в `InstallmentPlan.validate()` (новый метод `_autoset_branch`), порядок:
1. `self.branch` уже задан (кассир выбрал вручную) → оставить как есть.
2. иначе `sales_order` задан → `Sales Order.branch`.
3. иначе `stock_entry` задан → `Stock Entry.warehouse → Warehouse.branch`.
4. иначе по оператору `frappe.session.user`: если `_get_user_branches(user)` вернул
   **ровно один** филиал → подставить его.
5. иначе (у оператора несколько филиалов и ничего выше не сработало) → **`frappe.throw`**
   «Выберите филиал» (поле обязательно только в этом неоднозначном случае).
6. **Исключение:** unrestricted-пользователь (админ, `_is_unrestricted`) с 0 привязок Branch
   User — НЕ блокируем: оставляем пустым (иначе админ не сможет создать план).

Итог: кассир одного филиала — заполняется само, кликов ноль. Ручной выбор всплывает
только у многофилиального оператора.

### Секция 2 — НЕ делаем

Бэкфилл старых договоров **исключён** по решению мерчанта. Старые остаются с пустым
`branch`; отчёты обслуживают их старым путём через `sales_order` (fallback ниже).

### Секция 3 — переключить отчёты на прямое поле (с fallback)

Везде, где сейчас филиал плана резолвится как
`ip.sales_order IN (SELECT name FROM tabSales Order WHERE branch ...)`, заменить на «прямое
поле ИЛИ (поле пусто И старый путь)»:

```sql
(
  ip.branch IN (:branches)
  OR (
    (ip.branch IS NULL OR ip.branch = '')
    AND ip.sales_order IN (SELECT name FROM `tabSales Order` WHERE branch IN (:branches))
  )
)
```

Точки правки:
- **`installment_plan_query` + `has_installment_plan_permission`** (`permissions.py`) —
  НАСТОЯЩИЕ permission-хуки doctype (зарегистрированы в `hooks.py`): гейт Desk-списка и
  прав на чтение/запись (в т.ч. проведение платежа через `_require_doc_permission`).
  Читать `ip.branch` первым, при пустом → fallback на `sales_order`. В
  `has_installment_plan_permission` убрать ранний `if not doc.sales_order: return False`
  до проверки `branch` (иначе новые договоры с заполненным `branch`, но без заказа,
  блокировали бы branch-ограниченного кассира). **[добавлено после финального ревью]**
- **`sales_report.py`** — explicit branch filter + SELECT-колонка «Филиал» (тот же паттерн
  «поле ИЛИ SO» и `COALESCE(NULLIF(ip.branch,''), SO)`). **[добавлено после финального ревью]**
- **`_user_branch_clause`** (`bnpl_dashboard.py`) — ядро прав/фильтра; правка здесь
  автоматически чинит P&L-права (`_branch_clause_for`) и Collections-права.
- **`_PAYMENT_BRANCH_CASE`** (`profit.py`) — резолв филиала платежа по Installment Plan
  reference: сперва `ip.branch`, при пустом → `so.branch` через `ip.sales_order`
  (`COALESCE(ip.branch, (SELECT so.branch ...))`).
- **`_compute_accrual`** explicit branch filter (`profit.py`) — тот же паттерн «поле ИЛИ SO».
- **`collections_and_overdue.py`**: explicit branch filter (стр. 34) — паттерн «поле ИЛИ SO»;
  SELECT branch (стр. 43) — `COALESCE(ip.branch, (SELECT so.branch ... ))` чтобы колонка
  «Филиал» показывала прямое поле, а при пустом — старое через SO.

**Fallback гарантирует:** между деплоем и «прогревом» поля ничего не пропадает; старые
договоры ведут себя как сейчас; новые (с заполненным `branch`) — чинятся.

## Что меняется в поведении

| | Сейчас | После |
|---|---|---|
| Новые рассрочки под фильтром филиала | ❌ пропадают | ✅ видны |
| Старые рассрочки под фильтром филиала | ❌ пропадают | ❌ так же (не трогаем) |
| Режим «Все филиалы» | ✅ верно | ✅ верно (без изменений) |
| Кассир одного филиала (branch-restricted) | ❌ пустые списки | ✅ видит новые договоры своего филиала |

Суммы, график, прибыль, распределение — **не меняются**. Меняется только видимость под
фильтром филиала для новых договоров.

## Затрагиваемые файлы

- `nasiya365/nasiya365/doctype/installment_plan/installment_plan.json` — поле `branch`.
- `nasiya365/nasiya365/doctype/installment_plan/installment_plan.py` — `_autoset_branch` +
  вызов в `validate()`.
- `nasiya365/api/bnpl_dashboard.py` — `_user_branch_clause` (поле ИЛИ SO).
- `nasiya365/api/profit.py` — `_PAYMENT_BRANCH_CASE`, `_compute_accrual` explicit filter.
- `nasiya365/nasiya365/report/collections_and_overdue/collections_and_overdue.py` —
  explicit filter + SELECT branch.
- Тесты: `nasiya365/tests/test_plan_branch.py` (автозаполнение + fallback-резолв).

## Тесты (постоянные)

Автозаполнение (`_autoset_branch`):
1. ручной branch задан → не перезаписывается;
2. sales_order задан → берётся SO.branch;
3. stock_entry задан (без SO) → Warehouse.branch;
4. оператор с одним филиалом (без SO/stock_entry) → его филиал;
5. оператор с несколькими филиалами, ничего выше → throw «выберите филиал»;
6. unrestricted-админ без привязок → остаётся пустым, без throw.

Резолв в отчётах (fallback):
7. новый план с `branch=A`, без sales_order → виден под фильтром A, не виден под B;
8. старый план с пустым `branch`, но `sales_order→SO(branch=A)` → по-прежнему виден под A
   (fallback не сломан);
9. режим «Все филиалы» → оба видны (регресс: суммы не изменились).

## Открытые вопросы

Нет. (Ручной бэкфилл старых договоров — вне охвата; мерчант при желании проставит филиал
старым через интерфейс вручную позже.)
