
import frappe
import unittest
import os
from nasiya365.data_import import import_bnpl_data

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
            
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

