"""
Contract DocType Controller
Manages legal agreements and PDF generation
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today


class Contract(Document):
    def validate(self):
        self.set_contract_number()
        self.set_template()
        self.update_status()
    
    def set_contract_number(self):
        """Generate contract number if not set"""
        if not self.contract_number:
            self.contract_number = self.name
    
    def set_template(self):
        """Set default template if not specified"""
        if not self.template:
            from nasiya365.nasiya365.doctype.print_template.print_template import get_default_template
            template = get_default_template(self.contract_type)
            if template:
                self.template = template.name
    
    def update_status(self):
        """Update status based on signatures"""
        if self.signed_by_customer and self.signed_by_merchant:
            if self.status == "Draft":
                self.status = "Signed"
    
    def generate_pdf(self):
        """Generate PDF from template"""
        if not self.template:
            frappe.throw(_("Please select a print template"))
        
        from nasiya365.utils.pdf import generate_contract_pdf
        pdf_content = generate_contract_pdf(self.name)
        
        if pdf_content:
            # Save PDF as attachment
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
    contract.contract_type = "BNPL Agreement"
    contract.customer = plan.customer
    contract.installment_plan = plan.name
    contract.sales_order = plan.sales_order
    contract.total_amount = plan.total_amount
    contract.contract_date = today()
    contract.valid_until = plan.end_date
    contract.insert()
    
    return contract.name
