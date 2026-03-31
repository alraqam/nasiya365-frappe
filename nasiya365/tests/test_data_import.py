
import frappe
import unittest
import os
import shutil
import tempfile
from frappe.utils import flt
from nasiya365.data_import import import_bnpl_data, load_installment_counts_from_payments_csv

class TestDataImport(unittest.TestCase):
    def setUp(self):
        # Create dummy branch
        if not frappe.db.exists("Branch", "Test Branch"):
            frappe.get_doc({
                "doctype": "Branch",
                "branch_name": "Test Branch",
                "address": "Test Address",
                "city": "Test City"
            }).insert()
            
    def test_bnpl_import(self):
        # Create temp CSV
        csv_content = """Номер документа,Внутренний номер,Дата продажи,Клиент,Телефон,Код товара,Наименование товара,Цена продажи,Количество,IMEI,Общая сумма,Оплачено,Остаток долга,Количество платежей
881,1835,20.01.26,Yunusov Farruh,998977714474,T0535,Samsung S25 Ultra,"1,020,000",1.00,351589499794888,"1,020,000","300,000","720,000",7"""
        
        file_path = "test_import.csv"
        with open(file_path, "w", encoding="utf-8-sig") as f:
            f.write(csv_content)
            
        try:
            # Run import
            result = import_bnpl_data(file_path, "Test Branch")
            print(result)
            
            # Verify Customer
            customer = frappe.db.get_value("Customer Profile", {"first_name": "Yunusov", "last_name": "Farruh"}, "name")
            self.assertTrue(customer)
            
            # Verify Plan
            plan_name = frappe.db.get_value("Installment Plan", {"customer": customer}, "name")
            self.assertTrue(plan_name)
            
            plan = frappe.get_doc("Installment Plan", plan_name)
            self.assertEqual(plan.number_of_installments, 6) # 7 - 1 = 6
            self.assertEqual(flt(plan.principal_amount), 1020000.0)
            self.assertEqual(flt(plan.down_payment), 300000.0)
            self.assertEqual(len(plan.schedule or []), 6)
            
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    def test_load_installment_counts_from_payments_csv(self):
        content = (
            "Номер документа,Количество платежей\n"
            "898,7\n"
            "899,4\n"
        )
        path = os.path.join(tempfile.gettempdir(), "nasiya365_test_payments.csv")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(content)
        try:
            m = load_installment_counts_from_payments_csv(path)
            self.assertEqual(m.get("898"), 7)
            self.assertEqual(m.get("899"), 4)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_contract_import_uses_sidecar_installment_payments(self):
        """Contracts CSV without Количество платежей; counts from installment_payments.csv in same folder."""
        d = tempfile.mkdtemp()
        try:
            payments = os.path.join(d, "installment_payments.csv")
            contracts = os.path.join(d, "installment_contracts.csv")
            with open(payments, "w", encoding="utf-8-sig") as f:
                f.write("Номер документа,Количество платежей\n882,9\n")
            with open(contracts, "w", encoding="utf-8-sig") as f:
                f.write(
                    "Номер документа,Внутренний номер,Дата продажи,Клиент,Телефон,Код товара,"
                    "Наименование товара,Цена продажи,Количество,IMEI,Общая сумма,Оплачено,Остаток долга\n"
                    '882,1840,21.01.26,Sidecar Client,998977714476,T0536,Phone X,"500,000",1.00,'
                    '351589499794890,"500,000","100,000","400,000"\n'
                )
            result = import_bnpl_data(contracts, "Test Branch", import_type="Импорт договоров")
            self.assertIn("installment_payments.csv", result)
            customer = frappe.db.get_value(
                "Customer Profile", {"first_name": "Sidecar", "last_name": "Client"}, "name"
            )
            self.assertTrue(customer)
            plan_name = frappe.db.get_value("Installment Plan", {"customer": customer}, "name")
            self.assertTrue(plan_name)
            plan = frappe.get_doc("Installment Plan", plan_name)
            self.assertEqual(plan.number_of_installments, 8)
            self.assertEqual(len(plan.schedule or []), 8)
        finally:
            shutil.rmtree(d, ignore_errors=True)

