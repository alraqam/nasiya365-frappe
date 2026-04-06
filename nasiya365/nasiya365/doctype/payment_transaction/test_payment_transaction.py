import frappe
from frappe.tests.utils import FrappeTestCase

class TestPaymentTransaction(FrappeTestCase):
    def setUp(self):
        # Create a customer if not exists
        # Use phone to check existence as naming is auto-increment
        existing_customer = frappe.db.get_value("Customer Profile", {"phone": "+998901234567"}, "name")
        
        if existing_customer:
            self.customer_name = existing_customer
        else:
            c = frappe.get_doc({
                "doctype": "Customer Profile",
                "customer_name": "Test Customer",
                "phone": "+998901234567"
            })
            c.insert(ignore_permissions=True)
            self.customer_name = c.name

    def test_received_by_set_on_insert(self):
        doc = frappe.get_doc({
            "doctype": "Payment Transaction",
            "customer": self.customer_name,
            "payment_lines": [
                {"payment_method": "Наличные USD", "currency": "USD", "amount": 1000},
            ],
            "payment_method": "Наличные",
            "payment_date": frappe.utils.today(),
            "send_payment_sms": 0,
        })
        doc.insert()
        
        self.assertEqual(doc.received_by, frappe.session.user)

    def test_received_by_not_overwritten(self):
        other_user = "Administrator" # usually exists
        doc = frappe.get_doc({
            "doctype": "Payment Transaction",
            "customer": self.customer_name,
            "payment_lines": [
                {"payment_method": "Карта", "currency": "USD", "amount": 500},
            ],
            "payment_method": "Карта",
            "payment_date": frappe.utils.today(),
            "received_by": other_user,
            "send_payment_sms": 0,
        })
        doc.insert()
        self.assertEqual(doc.received_by, other_user)

    def test_split_channels_total_and_combined_method(self):
        from frappe.utils import flt
        doc = frappe.get_doc({
            "doctype": "Payment Transaction",
            "customer": self.customer_name,
            "payment_date": frappe.utils.today(),
            "exchange_rate": 12200,
            "payment_lines": [
                {"payment_method": "Наличные USD", "currency": "USD", "amount": 10},
                {"payment_method": "Наличные UZS", "currency": "UZS", "amount": 122000, "exchange_rate": 12200},
            ],
            "payment_method": "Наличные",
            "send_payment_sms": 0,
        })
        doc.insert()
        self.assertEqual(flt(doc.amount), 20)
        self.assertEqual(doc.payment_method, "Комбинированный")


    def tearDown(self):
        frappe.db.rollback()
