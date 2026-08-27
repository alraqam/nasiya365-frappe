# Гард переплаты по рассрочке — дизайн

**Дата:** 2026-08-26
**Область:** `nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py`
**Тип:** изменение поведения (валидация), защита от ошибки ввода

---

## Проблема

Оплата по рассрочке **больше остатка** договора сейчас **принимается**: гард стоит в
`on_submit` (после проведения) — `apply_payment` возвращает `excess`, система пишет
`overpayment_amount`, показывает оранжевое предупреждение и лог. Но платёж уже проведён,
деньги «приняты», предупреждение постфактум.

**Инцидент 2026-08 (INST-2026-00065):** кассир ввёл `660 000` в валюте **USD** вместо
**UZS** → излишек ≈ **$659 779**, договор «закрылся целиком». Потребовалось ручное
восстановление графика.

## Правило (согласовано с мерчантом)

**Платёж по рассрочке не должен превышать остаток по договору** (с копеечным допуском).
- Обычное погашение по графику и предоплата будущих периодов — в пределах остатка, ОК.
- Полное закрытие = внести ровно остаток, ОК.
- Всё, что **больше остатка** — ошибка ввода → **блок до проведения** с показом точного
  остатка. Без «подтверждения», без «обрезать и вернуть сдачу».

## Решение (Вариант A)

Серверный гард в `validate()` Payment Transaction — единый источник правды, ловит все пути
ввода (Desk-форма, кассовый визард, API), срабатывает **до** `on_submit`/аллокации.

### Точка внедрения

Новый метод `_guard_installment_overpayment()`, вызывается **последним** в `validate()`:

```python
def validate(self):
    self.autolink_single_open_installment_plan()
    self.apply_payment_totals()
    self._validate_payment_date()
    self._guard_installment_overpayment()   # NEW — reference и amount уже готовы
```

### Алгоритм `_guard_installment_overpayment()`

1. **Контекст-байпас:** если `frappe.flags.in_migrate` / `in_import` / `in_patch` /
   `in_install` — выйти (не ломать бэкфилы старых данных).
2. **Резолв плана** через общий хелпер `_resolve_installment_plan_name(self)`:
   - `reference_doctype == "Installment Plan"` → `reference_name`;
   - `reference_doctype == "Sales Order"` → `Installment Plan` с `sales_order == reference_name`;
   - иначе `None`.
   Нет плана → выйти (наличные продажи вне охвата).
3. `amt = flt(self.amount)`; если `amt <= 0` → выйти.
4. **Остаток:** `remaining = _installment_plan_remaining(plan_name)`:
   - primary: `remaining_balance` договора;
   - если `<= 0` или `NULL` → пересчёт из графика `SUM(amount − paid_amount)`, clamp ≥ 0.
5. **Проверка:** если `amt > remaining + TOLERANCE` (`TOLERANCE = 0.01`) → `frappe.throw(<msg>)`.

### Сообщение об ошибке

```
Сумма платежа {amt:.2f} превышает остаток по договору {plan}.
Остаток: {remaining:.2f} USD. Проверьте сумму — возможно, введена в сумах вместо USD.
Для полного закрытия введите {remaining:.2f}.
```

Явно называет вероятную причину (сумы вместо USD) и подсказывает точную сумму закрытия.

### Рефактор (DRY)

Логика «резолв плана из reference» продублирована в
`allocate_payment_transaction_to_installment_plan` (строки ~157–165). Вынести в модульный
хелпер `_resolve_installment_plan_name(doc)`; использовать в аллокации и в гарде.

### Что НЕ меняется

- `apply_payment`, аллокация, синк кассы, копеечный допуск на закрытие.
- Пост-обработка `excess` в `on_submit` — **остаётся** как страховка (defense-in-depth):
  после гарда почти не срабатывает, но ловит гонки/суб-копеечные случаи.
- Чистая прибыль, отчёты, распределение.

## Граничные случаи

| Случай | Поведение |
|---|---|
| `amt == remaining` (закрытие) | ОК |
| `amt < remaining` (график / предоплата будущих периодов) | ОК |
| `amt == remaining + 0.005` (округление) | ОК (в пределах допуска) |
| `amt >> remaining` (инцидент: 660000 vs 165.91) | **throw**, сообщение с остатком |
| Ссылка = Sales Order → план | резолвится и гардится |
| Закрытый план (`remaining == 0`) | любой `amt > 0.01` → **throw** |
| `remaining_balance` NULL/0, но график должен | берётся сумма графика (без ложного блока) |
| `in_migrate`/`in_import`/`in_patch`/`in_install` | без throw (байпас) |
| reference — наличная Sales Order без плана | без гарда (вне охвата) |

## Тесты (постоянные, `nasiya365/tests/`)

Новый файл `test_overpayment_guard.py` (или дополнение к тестам Payment Transaction):
1. закрытие ровно остатком — проходит;
2. платёж меньше остатка — проходит;
3. предоплата будущих периодов (в пределах остатка) — проходит;
4. округление `remaining + 0.005` — проходит;
5. **перебор (660000 vs ~165.91) — throws; текст содержит остаток;**
6. план через ссылку Sales Order — гард срабатывает;
7. закрытый план (remaining 0) — throws;
8. `frappe.flags.in_migrate = True` — не throws.

## Затрагиваемые файлы

- `nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py` — хелперы
  `_resolve_installment_plan_name`, `_installment_plan_remaining`, метод
  `_guard_installment_overpayment`, вызов в `validate`, рефактор аллокации.
- `nasiya365/tests/test_overpayment_guard.py` — регресс-тесты.

## Открытые вопросы

Нет. (Опциональный эскейп-хэтч `self.flags.ignore_overpayment_guard` для скриптовых
корректировок админом — можно добавить позже, если понадобится; в текущий охват не входит.)
