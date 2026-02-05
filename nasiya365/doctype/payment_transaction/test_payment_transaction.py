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
            "amount": 1000,
            "payment_method": "Cash",
            "payment_date": frappe.utils.today()
        })
        doc.insert()
        
        self.assertEqual(doc.received_by, frappe.session.user)

    def test_received_by_not_overwritten(self):
        other_user = "Administrator" # usually exists
        doc = frappe.get_doc({
            "doctype": "Payment Transaction",
            "customer": self.customer_name,
            "amount": 500,
            "payment_method": "Card",
            "payment_date": frappe.utils.today(),
            "received_by": other_user
        })
        doc.insert()
        self.assertEqual(doc.received_by, other_user)


    def tearDown(self):
        frappe.db.rollback()
