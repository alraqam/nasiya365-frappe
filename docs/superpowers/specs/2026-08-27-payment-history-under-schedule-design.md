# История оплат под графиком — дизайн

**Дата:** 2026-08-27
**Область:** форма Installment Plan (просмотр)
**Тип:** новая фича, только чтение (read-only представление)
**Макет:** https://claude.ai/code/artifact/b5e2d7e1-f63d-4e17-b493-11a36a45d703

---

## Запрос мерчанта

Клиент по графику платит 1-го числа, но до срока носит деньги **частями** (напр. 10-го,
20-го, 28-го). Нужна таблица **истории фактических оплат по датам** — на форме договора,
под таблицей графика, чтобы видеть, как деньги реально приносили.

## Ключевое решение — представление, не хранилище

Каждый частичный платёж **уже** хранится как отдельный документ `Payment Transaction`
(дата, сумма, метод). Второе хранилище-копию НЕ заводим (риск рассинхрона при
оплате/отмене/правке). Показываем **выписку** — read-only таблицу, которая читает
существующие платежи вживую. Это совпадает с подходом кода (`installment_plan.js`:
«новых полей в базе не заводим»).

## Что показываем (согласовано)

Колонки: **Дата · Сумма · Метод · № платежа**; сортировка по дате по возрастанию (как
приносили); внизу итог «Всего оплачено · N платежей». Только **проведённые** платежи
(`docstatus=1`, `status='Завершен'`). Отменённые/черновики не показываем.

## Реализация

### 1. HTML-поле на форме (контейнер рендера, в БД ничего не хранит)

В `installment_plan.json`: новое поле
```json
{ "fieldname": "payment_history_html", "fieldtype": "HTML", "label": "История оплат" }
```
Добавить в `field_order` сразу ПОСЛЕ `schedule` (idx 31, перед `progress_section`).

### 2. Backend — whitelisted метод

В `installment_plan.py`:
```python
@frappe.whitelist()
def get_payment_history(installment_plan):
    """Проведённые платежи по договору для выписки под графиком (read-only)."""
```
Логика:
- выбрать `Payment Transaction` где `reference_doctype='Installment Plan'`,
  `reference_name=installment_plan`, `docstatus=1`, `status='Завершен'`,
  поля `name, payment_date, amount`, `ORDER BY payment_date ASC, creation ASC`;
- метод для каждого: distinct `payment_method` из `payment_lines` (child) —
  1 метод → он; >1 → `"Комбинированный"`; 0 строк → header `payment_method` или `"—"`;
- вернуть список `{name, payment_date, amount, method}` + `total` (сумма) + `count`.

### 3. Client JS — рендер

В `installment_plan.js`, в существующем `refresh(frm)`: вызвать `get_payment_history`
и отрисовать HTML-таблицу в `frm.get_field('payment_history_html').$wrapper`
(Дата · Сумма · Метод · № платежа + строка итога). Пустой результат → «Оплат пока нет».
Формат суммы — через существующий `format_currency` (системная валюта), даты — локальный формат.

## Свойства / что НЕ меняется

- Только чтение: график, суммы, аллокация, прибыль — не затрагиваются.
- Данные всегда актуальны (читаются при каждом открытии формы).
- Новых записей/хранилища в БД нет (HTML-поле данные не хранит).

## Затрагиваемые файлы

- `nasiya365/nasiya365/doctype/installment_plan/installment_plan.json` — поле + field_order.
- `nasiya365/nasiya365/doctype/installment_plan/installment_plan.py` — `get_payment_history`.
- `nasiya365/nasiya365/doctype/installment_plan/installment_plan.js` — рендер в refresh.
- `nasiya365/tests/test_payment_history.py` — тест backend-метода.

## Тесты (backend `get_payment_history`)

1. Договор с 3 проведёнными платежами разных дат → возвращает 3, отсортированы по дате,
   `total` = сумма, `count` = 3.
2. Метод: платёж с одной строкой `payment_lines` → её метод; с двумя разными →
   `"Комбинированный"`.
3. Отменённый (`docstatus=2`) / черновик (`docstatus=0`) платёж → НЕ включён.
4. Договор без платежей → пустой список, `total=0`, `count=0`.

## Открытые вопросы

Нет. (Экспорт/печать выписки, фильтр по датам — вне охвата; при желании позже.)
