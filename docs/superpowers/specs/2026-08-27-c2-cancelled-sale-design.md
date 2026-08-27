# C2 — отменённая наличная продажа даёт фантомную прибыль — дизайн

**Дата:** 2026-08-27
**Область:** Sales Order (отмена) + движки P&L
**Тип:** исправление бага (расхождение отчётов)

---

## Проблема (root cause, подтверждён на текущем коде и данных)

При отмене наличной продажи (`SalesOrder.on_cancel`) склад реверсится, но
**авто-созданный платёж (`create_cash_receipt`) НЕ отменяется** — остаётся `docstatus=1`,
`status='Завершен'`. Плюс движки прибыли не проверяют docstatus заказа:
- `_compute_cash` (ветка Sales Order) — резолвит SO без проверки docstatus;
- `_compute_cost_recovery` (Раздел 2, ветка Sales Order) — то же;
- `_compute_accrual` — уже проверяет `so.docstatus=1` (корректно).

Итог: отменённая продажа продолжает давать прибыль в P&L и висит в кассе, а Sales Report
(проверяет docstatus=1) её убирает → отчёты расходятся. **На dev подтверждено: 1
отменённый SO с висящим проведённым платежом.**

## Решение (согласовано)

**Часть 1 — при отмене заказа отменять его платёж (going forward).**
**Часть 2 — движки P&L не считают отменённые заказы (старые данные + защита).**
Старые платежи физически НЕ отменяем (по решению мерчанта) — только отчёты их игнорируют.

### Часть 1 — каскадная отмена платежа в `SalesOrder.on_cancel`

Добавить вызов нового метода в конце `on_cancel`:

```python
    def on_cancel(self):
        self.status = "Отменен"
        self.db_update()
        self.reverse_stock()
        self._cancel_linked_cash_receipt()   # NEW
```

Метод:
```python
    def _cancel_linked_cash_receipt(self):
        """Отменить проведённый наличный платёж этого заказа (реверс кассы/разноски
        через Payment Transaction.on_cancel). Идемпотентно: уже отменённые/отсутствующие
        пропускаются."""
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

Payment Transaction.on_cancel уже реверсит кассу (`_remove_payment_from_cashbox`) и разноску
(`_deallocate_payment_from_installment_plan` — для чистого cash SO без плана это no-op).

### Часть 2 — docstatus-фильтр в движках

В `_compute_cash` (ветка `elif pay.rdt == "Sales Order"`) и `_compute_cost_recovery`
(ветка `elif w.rdt == "Sales Order"`) — добавить `docstatus` в выборку и пропускать
отменённые:

```python
                so = frappe.db.get_value(
                    "Sales Order", <pay.rn|w.rn>, ["name", "total_amount", "docstatus"], as_dict=True)
                so_cache[...] = so
            if not so or so.docstatus == 2:
                continue
```

`_compute_accrual` не трогаем (уже фильтрует `so.docstatus=1`).

## Что НЕ меняется

- Нормальные продажи (docstatus=1), рассрочка, суммы, график — без изменений.
- Старые висящие платежи физически не отменяются; касса за прошлые периоды не переписывается.
- Installment Plan путь отмены — не трогаем.

## Тесты

**Часть 2 (движок, изолированно):**
1. Отменённый SO (docstatus=2) + проведённый платёж в периоде → `_compute_cash` НЕ включает
   его в `cash_revenue`/`cash_margin`.
2. Нормальный SO (docstatus=1) + платёж → включается (регресс, как раньше).
3. Cost-recovery: отменённый SO с платежом в окне → не даёт признанной маржи.

**Часть 1 (каскадная отмена):**
4. `_cancel_linked_cash_receipt`: заказ с проведённым платежом → после вызова платёж
   `docstatus=2`.
5. Идемпотентность: платёж уже отменён / заказ без платежа → без ошибки, ничего не ломает.

## Затрагиваемые файлы

- `nasiya365/nasiya365/doctype/sales_order/sales_order.py` — `_cancel_linked_cash_receipt`
  + вызов в `on_cancel`.
- `nasiya365/api/profit.py` — docstatus-фильтр в `_compute_cash` и `_compute_cost_recovery`.
- `nasiya365/tests/test_c2_cancelled_sale.py` — тесты.

## Открытые вопросы

Нет. (Разовый патч для старых висящих платежей — вне охвата по решению мерчанта; защита
в отчётах закрывает их для P&L.)
