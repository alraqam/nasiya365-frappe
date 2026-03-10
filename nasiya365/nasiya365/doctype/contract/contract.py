"""
Contract DocType Controller
Manages legal agreements, device protection, and financial tracking
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today, getdate, flt


class Contract(Document):
    def validate(self):
        self.set_contract_number()
        self.set_template()
        self.update_status()
        self.set_product_and_imei()
        self.set_financial_summary()
    
    def onload(self):
        """Refresh financial summary on every document load"""
        self.set_financial_summary()
    
    def set_contract_number(self):
        """Generate contract number if not set"""
        if not self.contract_number:
            self.contract_number = self.name
    
    def set_template(self):
        """Set default template if not specified"""
        if not self.template:
            from nasiya365.nasiya365.doctype.print_template.print_template import get_default_template
            template_type = "Договор"
            template = get_default_template(template_type)
            if template:
                self.template = template.name
    
    def update_status(self):
        """Update status based on signatures"""
        if self.signed_by_customer and self.signed_by_merchant:
            if self.status == "Черновик":
                self.status = "Подписан"
    
    def set_product_and_imei(self):
        """Fetch product name and IMEI (last 6 digits) from linked Sales Order"""
        if self.sales_order and (not self.product_name or not self.imei):
            items = frappe.get_all(
                "Sales Order Item",
                filters={"parent": self.sales_order},
                fields=["product_name", "imei"],
                order_by="idx asc",
                limit=1
            )
            if items:
                if not self.product_name and items[0].product_name:
                    self.product_name = items[0].product_name
                if not self.imei and items[0].imei:
                    # Store only last 6 digits
                    full_imei = items[0].imei.strip()
                    self.imei = full_imei[-6:] if len(full_imei) >= 6 else full_imei
    
    def set_financial_summary(self):
        """Compute financial summary from linked Installment Plan"""
        if not self.installment_plan:
            return
        
        plan = frappe.db.get_value(
            "Installment Plan",
            self.installment_plan,
            ["remaining_balance", "installment_amount"],
            as_dict=True
        )
        
        if plan:
            self.total_debt = flt(plan.remaining_balance)
            self.monthly_payment = flt(plan.installment_amount)
        
        # Compute debt_today: sum of unpaid amounts where due_date <= today
        schedule_rows = frappe.get_all(
            "Installment Schedule",
            filters={
                "parent": self.installment_plan,
                "due_date": ["<=", today()],
                "status": ["in", ["Ожидает", "Просрочен", "Частично"]]
            },
            fields=["amount", "paid_amount"]
        )
        
        debt_today = 0
        for row in schedule_rows:
            debt_today += flt(row.amount) - flt(row.paid_amount)
        
        self.debt_today = debt_today
    
    def generate_pdf(self):
        """Generate PDF from template"""
        if not self.template:
            frappe.throw(_("Пожалуйста, выберите шаблон печати"))
        
        from nasiya365.utils.pdf import generate_contract_pdf
        pdf_content = generate_contract_pdf(self.name)
        
        if pdf_content:
            file_name = f"Contract-{self.name}.pdf"
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": file_name,
                "attached_to_doctype": "Contract",
                "attached_to_name": self.name,
                "content": pdf_content,
                "is_private": 1
            })
            file_doc.insert()
            
            self.pdf_file = file_doc.file_url
            self.save()
            
            return file_doc.file_url
        
        return None


@frappe.whitelist()
def generate_contract_pdf_api(contract_name):
    """API endpoint to generate contract PDF"""
    contract = frappe.get_doc("Contract", contract_name)
    return contract.generate_pdf()


@frappe.whitelist()
def create_contract_from_plan(installment_plan_name):
    """Create a contract document from an installment plan"""
    plan = frappe.get_doc("Installment Plan", installment_plan_name)
    
    contract = frappe.new_doc("Contract")
    contract.contract_type = "Рассрочка (BNPL)"
    contract.customer = plan.customer
    contract.installment_plan = plan.name
    contract.sales_order = plan.sales_order
    contract.total_amount = plan.total_amount
    contract.contract_date = today()
    contract.valid_until = plan.end_date
    
    # Populate financial summary
    contract.total_debt = flt(plan.remaining_balance)
    contract.monthly_payment = flt(plan.installment_amount)
    
    # Product name and IMEI will be fetched during validate via set_product_and_imei
    
    contract.insert()
    
    return contract.name

