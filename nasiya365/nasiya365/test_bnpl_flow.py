import frappe
from frappe.utils import add_days, today

def run_test():
    frappe.db.begin()
    
    try:
        # Create a test customer
        customer = frappe.new_doc("Customer Profile")
        customer.first_name = "Test"
        customer.last_name = "User"
        customer.status = "Активный"
        customer.message_language = "Узбекский"
        
        customer.append("phone_numbers", {
            "phone_number": "+998901234567",
            "phone_type": "Мобильный",
            "is_primary": 1
        })
        
        # Test missing limit logic added
        customer.credit_limit = 10000000  # 10M UZS
        customer.insert()
        customer.submit() if getattr(customer, 'submit', None) else customer.save()
        
        # Create unique product to avoid link validation errors with draft products
        import random
        rand_id = str(random.randint(1000, 9999))
        product = frappe.new_doc("Product")
        product.product_name = "Test Product " + rand_id
        product.sku = "TEST-PRD-" + rand_id
        product.product_category = "Electronics"  # Must exist? Let's just create raw
        product.selling_price = 5000000 # 5M UZS
        product.allow_installment = 1
        product.insert(ignore_permissions=True, ignore_mandatory=True)
        try:
            product.submit()
        except:
            pass
            
        prd_name = product.name
        print(f"Using Mock Product: {prd_name}")
        
        # Create Sales Order
        so = frappe.new_doc("Sales Order")
        so.customer = customer.name
        so.status = "Черновик"
        wh_name = frappe.db.get_value("Warehouse", {}, "name")
        if not wh_name:
            wh = frappe.new_doc("Warehouse")
            wh.warehouse_name = "Test WH"
            wh.insert()
            wh_name = wh.name
            
        so.branch = frappe.db.get_value("Branch", {}, "name") # Assuming there's a branch
        so.warehouse = wh_name
        
        so.append("items", {
            "product": prd_name,
            "quantity": 1,
            "unit_price": 5000000,
            "discount_percent": 0
        })
        
        so.paid_amount = 1000000 # 1M UZS Down payment
        # Should calculate items and totals
        so.insert()
        so.submit()
        
        print(f"Sales Order Total Amount: {so.total_amount}")
        print(f"Sales Order Paid Amount (Down Payment): {so.paid_amount}")
        
        plan_name = getattr(so, "installment_plan", None)
        if not plan_name:
            print("ERROR: Installment Plan was not created for the Sales Order.")
            return False
            
        plan = frappe.get_doc("Installment Plan", plan_name)
        print(f"Plan Principal Amount: {plan.principal_amount}")
        print(f"Plan Financed Amount: {plan.financed_amount}")
        
        if plan.principal_amount != so.total_amount:
            print("ERROR: Plan Principal Amount != SO Total Amount")
            
        print(f"Plan Total Amount: {plan.total_amount}")
        print(f"Plan Installment Amount: {plan.installment_amount}")
        
        # Verify Customer Available Limit
        customer.reload()
        print(f"Customer Initial Credit Limit: {customer.credit_limit}")
        print(f"Customer Total Debt: {customer.total_debt}")
        print(f"Customer Available Limit: {customer.available_limit}")
        
        # Make a Payment
        payment = frappe.new_doc("Payment Transaction")
        payment.reference_doctype = "Installment Plan"
        payment.reference_name = plan.name
        payment.customer = customer.name
        payment.amount = plan.installment_amount
        payment.payment_method = "Наличные"
        payment.status = "Завершен"
        payment.insert()
        payment.submit() if getattr(payment, 'submit', None) else payment.save()
        
        # Check Plan Schedules
        plan.reload()
        paid_schedules = [s for s in plan.schedule if s.status == "Оплачен"]
        print(f"Paid Schedules Count: {len(paid_schedules)}")
        
        if len(paid_schedules) == 1:
            print("Payment Transaction logic working correctly!")
        else:
            print("ERROR: Payment was not applied to Installment Schedules properly.")
            
        return True
        
    except Exception as e:
        print(f"Exception during test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Rollback all test data
        frappe.db.rollback()
        print("Test data rolled back.")

