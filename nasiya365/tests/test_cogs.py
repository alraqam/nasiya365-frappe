"""Себестоимость проданного (§6 аудита).

Шесть дефектов, найденных перепроверкой:

1. Черновые поступления попадали в расчёт: `se.docstatus < 2`.
2. Тип документа не проверялся — себестоимость могла прийти из отпуска,
   перемещения или корректировки.
3. Поиск по последним шести цифрам IMEI как финансовый источник: два разных
   аппарата с совпадающим хвостом давали цену друг друга.
4. Если поступления на дату продажи не нашлось, брался самый свежий вообще —
   в том числе закупленный ПОЗЖЕ продажи.
5. Количество не умножалось: продажа пяти аксессуаров стоила как одна штука.
6. Ненайденная себестоимость молча превращалась в ноль, и сделка выглядела
   стопроцентно маржинальной.
"""

import unittest

import frappe
from frappe.utils import flt

from nasiya365.api.profit import _cogs_for_sale_item, _cogs_for_sales_order

_SALE_DATE = "2026-06-01"


def _db_insert(doctype, **fields):
    doc = frappe.get_doc({"doctype": doctype, **fields})
    doc.name = frappe.generate_hash(length=10)
    doc.db_insert()
    return doc


def _receipt(imei, amount, expense=0, quantity=1, posting_date="2026-05-01",
             docstatus=1, entry_type="Поступление"):
    se = _db_insert("Stock Entry", entry_type=entry_type, posting_date=posting_date,
                    posting_time="10:00:00", docstatus=docstatus)
    sei = _db_insert("Stock Entry Item", parent=se.name, parenttype="Stock Entry",
                     parentfield="items", idx=1, imei=imei, quantity=quantity,
                     rate=amount / quantity if quantity else amount,
                     amount=amount, expense=expense)
    return se, sei


def _imei():
    return "35" + frappe.generate_hash(length=6).upper()


class TestCogsLookup(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("cogs")

    def tearDown(self):
        frappe.db.rollback(save_point="cogs")

    def test_submitted_receipt_gives_cost_with_expenses(self):
        imei = _imei()
        _receipt(imei, amount=400, expense=25)
        self.assertAlmostEqual(_cogs_for_sale_item(imei, as_of_date=_SALE_DATE), 425, places=2)

    def test_draft_receipt_is_not_a_cost_source(self):
        imei = _imei()
        _receipt(imei, amount=400, docstatus=0)
        self.assertIsNone(_cogs_for_sale_item(imei, as_of_date=_SALE_DATE))

    def test_only_purchases_count(self):
        for entry_type in ("Отпуск", "Перемещение", "Корректировка"):
            imei = _imei()
            _receipt(imei, amount=400, entry_type=entry_type)
            self.assertIsNone(_cogs_for_sale_item(imei, as_of_date=_SALE_DATE),
                              f"тип {entry_type} принят за закупку")

    def test_similar_imei_tail_is_not_a_match(self):
        """Два аппарата с одинаковыми последними шестью цифрами."""
        _receipt("351111222333", amount=400)
        self.assertIsNone(_cogs_for_sale_item("359999222333", as_of_date=_SALE_DATE))

    def test_purchase_after_the_sale_is_not_used(self):
        imei = _imei()
        _receipt(imei, amount=400, posting_date="2026-07-01")   # позже продажи
        self.assertIsNone(_cogs_for_sale_item(imei, as_of_date=_SALE_DATE))

    def test_latest_purchase_on_or_before_the_sale_wins(self):
        """Аппарат купили, продали, купили снова — берётся тот цикл."""
        imei = _imei()
        _receipt(imei, amount=300, posting_date="2026-01-01")
        _receipt(imei, amount=450, posting_date="2026-05-15")
        self.assertAlmostEqual(_cogs_for_sale_item(imei, as_of_date=_SALE_DATE), 450, places=2)

    def test_missing_cost_is_not_zero(self):
        self.assertIsNone(_cogs_for_sale_item(_imei(), as_of_date=_SALE_DATE))

    def test_bulk_receipt_gives_cost_per_unit(self):
        imei = _imei()
        _receipt(imei, amount=1000, expense=200, quantity=10)
        self.assertAlmostEqual(_cogs_for_sale_item(imei, as_of_date=_SALE_DATE), 120, places=2)

    def test_quantity_multiplies_the_unit_cost(self):
        imei = _imei()
        _receipt(imei, amount=1000, expense=200, quantity=10)
        self.assertAlmostEqual(
            _cogs_for_sale_item(imei, as_of_date=_SALE_DATE, quantity=5), 600, places=2)


class TestCogsForOrder(unittest.TestCase):
    def setUp(self):
        frappe.db.savepoint("cogs_order")

    def tearDown(self):
        frappe.db.rollback(save_point="cogs_order")

    def _order(self, lines):
        so = _db_insert("Sales Order", order_date=_SALE_DATE, docstatus=1,
                        total_amount=sum(a for _, _, a in lines))
        for i, (imei, quantity, _) in enumerate(lines):
            _db_insert("Sales Order Item", parent=so.name, parenttype="Sales Order",
                       parentfield="items", idx=i + 1, imei=imei, quantity=quantity,
                       unit_price=100, amount=100 * quantity)
        return so

    def test_order_sums_cost_across_lines(self):
        a, b = _imei(), _imei()
        _receipt(a, amount=300)
        _receipt(b, amount=200)
        so = self._order([(a, 1, 100), (b, 1, 100)])
        self.assertAlmostEqual(_cogs_for_sales_order(so.name), 500, places=2)

    def test_order_multiplies_by_quantity(self):
        """Пять аксессуаров стоят впятеро — раньше считались как один."""
        acc = _imei()
        _receipt(acc, amount=500, quantity=10)   # 50 за штуку
        so = self._order([(acc, 5, 500)])
        self.assertAlmostEqual(_cogs_for_sales_order(so.name), 250, places=2)

    def test_unresolved_line_does_not_silently_become_zero(self):
        known = _imei()
        _receipt(known, amount=300)
        so = self._order([(known, 1, 100), (_imei(), 1, 100)])
        cost, unresolved = _cogs_for_sales_order(so.name, with_diagnostics=True)
        self.assertAlmostEqual(cost, 300, places=2)
        self.assertEqual(unresolved, 1)
