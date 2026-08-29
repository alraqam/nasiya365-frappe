# C5 — списанный план даёт прибыль после списания — дизайн

**Дата:** 2026-08-29
**Область:** движки P&L (cash + cost-recovery)
**Тип:** исправление бага (расхождение отчётов)

---

## Проблема (root cause подтверждён на текущем коде)

План со статусом `"Списан"` (безнадёжный долг) корректно выпадает из Sales Report,
Collections & Overdue и P&L accrual (`_compute_accrual` использует allow-list
`_LIVE_PLAN_STATUSES = ("Активный", "Просрочен", "Завершен")`). **Но** `_compute_cash`
и `_compute_cost_recovery` (Раздел 2) всё ещё признают исторически собранные по нему
платежи как прибыль — оба проверяют только `contract_status == "Отменен"`, но НЕ
`status == "Списан"`.

Итог: списанный план продолжает давать маржу в P&L, тогда как все остальные отчёты
считают его исчезнувшим.

## Решение (согласовано)

Добавить фильтр `status == "Списан"` в оба движка — той же формой `or`, что уже
используется для `contract_status == "Отменен"`. Списанный план **полностью**
исключается из прибыли (консистентно с accrual/Sales Report/Collections).

Нюанс (подтверждён, принят): в системе **нет даты списания** (`status` — простой Select
без timestamp; `apply_payment` и так запрещает платежи на списанный план), поэтому фикс
обнуляет **всю** историческую cash/cost-recovery прибыль по такому плану — «только после
списания» технически невозможно и не требуется.

### Точки правки (`nasiya365/api/profit.py`)

**`_compute_cash`** (ветка Installment Plan) — `status` уже в `get_value`, менять только условие:
```python
            if not plan or (plan.contract_status == "Отменен"):
                continue
```
→
```python
            if not plan or plan.contract_status == "Отменен" or plan.status == "Списан":
                continue
```

**`_compute_cost_recovery`** (ветка Installment Plan) — `status` НЕ в выборке, добавить + условие:
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
→
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

`collected_total` уже скипается тем же `continue` (после проверки) — отдельной правки не нужно.

## Что НЕ меняется

`_compute_accrual` (уже исключает списанные через allow-list), суммы/график, Installment
Plan путь, статусы. Только cash/cost-recovery перестают признавать платежи списанных планов.

## Тесты

Изолированный период **2030** (вне demo-данных dev 2026-06..2026-10; см. паттерн
`test_cost_recovery_engine`):
1. Списанный план (`status="Списан"`) + платёж в периоде → `_compute_cash` НЕ даёт
   `financed_margin`/`interest_income` по нему.
2. Списанный план + платёж → `_compute_cost_recovery` не даёт признанной маржи.
3. Активный план (`status="Активный"`) + платёж → считается как раньше (регресс).

## Затрагиваемые файлы

- `nasiya365/api/profit.py` — условие в `_compute_cash` и `_compute_cost_recovery`.
- `nasiya365/tests/test_c5_writeoff_profit.py` — тесты.

## Открытые вопросы

Нет.
