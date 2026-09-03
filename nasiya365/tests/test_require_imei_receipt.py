import unittest
import frappe


def _db_insert(doctype, **fields):
	doc = frappe.get_doc({"doctype": doctype, **fields})
	doc.name = frappe.generate_hash(length=10)
	doc.db_insert()
	return doc


def _product(allow_installment):
	return _db_insert("Product", product_name="T-" + frappe.generate_hash(length=5),
	                  allow_installment=allow_installment, is_active=1)


def _ste(entry_type, rows):
	"""In-memory Stock Entry (не сохраняется) для прямого вызова проверки.
	rows = список dict со ссылкой на product и imei."""
	doc = frappe.get_doc({"doctype": "Stock Entry", "entry_type": entry_type})
	for i, r in enumerate(rows, start=1):
		doc.append("items", {"product": r["product"], "imei": r.get("imei", ""),
		                     "quantity": 1, "idx": i})
	return doc


class TestRequireImeiReceipt(unittest.TestCase):
	def setUp(self):
		frappe.db.savepoint("req_imei")

	def tearDown(self):
		frappe.db.rollback(save_point="req_imei")

	def test_phone_receipt_without_imei_throws(self):
		phone = _product(allow_installment=1)
		doc = _ste("Поступление", [{"product": phone.name, "imei": ""}])
		with self.assertRaises(frappe.exceptions.ValidationError):
			doc._require_imei_for_phones()

	def test_phone_receipt_with_imei_ok(self):
		phone = _product(allow_installment=1)
		doc = _ste("Поступление", [{"product": phone.name, "imei": "353898106937998"}])
		doc._require_imei_for_phones()  # без исключения

	def test_accessory_receipt_without_imei_ok(self):
		acc = _product(allow_installment=0)
		doc = _ste("Поступление", [{"product": acc.name, "imei": ""}])
		doc._require_imei_for_phones()  # без исключения

	def test_non_receipt_type_not_checked(self):
		phone = _product(allow_installment=1)
		doc = _ste("Отпуск", [{"product": phone.name, "imei": ""}])
		doc._require_imei_for_phones()  # не Поступление → не блокируем

	def test_mixed_rows_phone_ok_accessory_empty_ok(self):
		phone = _product(allow_installment=1)
		acc = _product(allow_installment=0)
		doc = _ste("Поступление", [
			{"product": phone.name, "imei": "353898106937998"},
			{"product": acc.name, "imei": ""},
		])
		doc._require_imei_for_phones()  # телефон с IMEI, аксессуар без — ОК
