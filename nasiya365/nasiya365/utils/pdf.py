"""
PDF Generation Utility for Nasiya365
Uses WeasyPrint to generate PDFs from Jinja2 templates
"""

import frappe
from frappe import _
from jinja2 import Template


def generate_contract_pdf(contract_name):
    """
    Generate PDF for a contract using its print template
    
    Args:
        contract_name: Name of the Contract document
    
    Returns:
        bytes: PDF content or None if failed
    """
    contract = frappe.get_doc("Contract", contract_name)
    
    if not contract.template:
        frappe.throw(_("Шаблон печати не выбран для этого договора"))
    
    template_doc = frappe.get_doc("Print Template", contract.template)
    
    # Gather all context data
    context = get_template_context(contract)
    
    # Render template
    html_content = render_template(template_doc, context)
    
    # Generate PDF
    try:
        from weasyprint import HTML, CSS
        
        # Create base CSS
        base_css = get_base_css(template_doc)
        
        # Add custom CSS from template
        if template_doc.css_styles:
            base_css += template_doc.css_styles
        
        html = HTML(string=html_content)
        css = CSS(string=base_css)
        
        pdf_bytes = html.write_pdf(stylesheets=[css])
        return pdf_bytes
        
    except ImportError:
        frappe.log_error("WeasyPrint not installed", "PDF Generation Error")
        frappe.throw(_("Библиотека генерации PDF недоступна"))
    except Exception as e:
        frappe.log_error(str(e), "PDF Generation Error")
        frappe.throw(_("Ошибка при генерации PDF: {0}").format(str(e)))


def get_template_context(contract):
    """
    Build the context dictionary for template rendering
    """
    context = {
        "contract": contract.as_dict()
    }
    
    # Customer data
    if contract.customer:
        customer = frappe.get_doc("Customer Profile", contract.customer)
        context["customer"] = customer.as_dict()
    
    # Installment plan data
    if contract.installment_plan:
        plan = frappe.get_doc("Installment Plan", contract.installment_plan)
        context["installment_plan"] = plan.as_dict()
        context["installment_plan"]["schedule"] = [s.as_dict() for s in plan.schedule]
    
    # Sales order and items
    if contract.sales_order:
        sales_order = frappe.get_doc("Sales Order", contract.sales_order)
        context["sales_order"] = sales_order.as_dict()
        context["items"] = [item.as_dict() for item in sales_order.items]
    else:
        context["items"] = []
    
    # Merchant settings
    settings = frappe.get_single("Merchant Settings")
    context["merchant"] = settings.as_dict()
    
    # Format helpers
    context["today"] = frappe.utils.today()
    context["now"] = frappe.utils.now()
    
    return context


def render_template(template_doc, context):
    """
    Render the HTML template with Jinja2
    """
    # Build full HTML document
    html_parts = []
    
    html_parts.append("<!DOCTYPE html>")
    html_parts.append("<html>")
    html_parts.append("<head>")
    html_parts.append('<meta charset="UTF-8">')
    html_parts.append(f"<title>Contract {context['contract']['name']}</title>")
    html_parts.append("</head>")
    html_parts.append("<body>")
    
    # Render header
    if template_doc.header_html:
        header_template = Template(template_doc.header_html)
        html_parts.append('<div class="header">')
        html_parts.append(header_template.render(**context))
        html_parts.append("</div>")
    
    # Render body
    body_template = Template(template_doc.body_html)
    html_parts.append('<div class="body">')
    html_parts.append(body_template.render(**context))
    html_parts.append("</div>")
    
    # Render footer
    if template_doc.footer_html:
        footer_template = Template(template_doc.footer_html)
        html_parts.append('<div class="footer">')
        html_parts.append(footer_template.render(**context))
        html_parts.append("</div>")
    
    html_parts.append("</body>")
    html_parts.append("</html>")
    
    return "\n".join(html_parts)


def get_base_css(template_doc):
    """
    Get base CSS for PDF generation
    """
    page_size = template_doc.page_size or "A4"
    orientation = template_doc.orientation or "portrait"
    
    return f"""
    @page {{
        size: {page_size} {orientation.lower()};
        margin: 2cm;
        
        @top-center {{
            content: element(header);
        }}
        
        @bottom-center {{
            content: element(footer);
        }}
    }}
    
    body {{
        font-family: 'DejaVu Sans', Arial, sans-serif;
        font-size: 12pt;
        line-height: 1.5;
        color: #333;
    }}
    
    .header {{
        position: running(header);
        text-align: center;
        padding-bottom: 10px;
        border-bottom: 1px solid #ccc;
    }}
    
    .footer {{
        position: running(footer);
        text-align: center;
        font-size: 10pt;
        color: #666;
        padding-top: 10px;
        border-top: 1px solid #ccc;
    }}
    
    table {{
        width: 100%;
        border-collapse: collapse;
        margin: 15px 0;
    }}
    
    th, td {{
        border: 1px solid #ddd;
        padding: 8px;
        text-align: left;
    }}
    
    th {{
        background-color: #f5f5f5;
        font-weight: bold;
    }}
    
    .signatures {{
        display: flex;
        justify-content: space-between;
        margin-top: 50px;
    }}
    
    .signature-box {{
        width: 45%;
    }}
    
    .signature-line {{
        border-bottom: 1px solid #333;
        height: 40px;
        margin-top: 10px;
    }}
    
    h1 {{ font-size: 18pt; }}
    h2 {{ font-size: 16pt; }}
    h3 {{ font-size: 14pt; }}
    
    .text-center {{ text-align: center; }}
    .text-right {{ text-align: right; }}
    .bold {{ font-weight: bold; }}
    """


@frappe.whitelist()
def preview_template(template_name, sample_data=None):
    """
    Preview a print template with sample data
    """
    template_doc = frappe.get_doc("Print Template", template_name)
    
    # Use sample data or generate mock data
    context = sample_data or generate_sample_context()
    
    html_content = render_template(template_doc, context)
    
    return {
        "html": html_content,
        "css": template_doc.css_styles or ""
    }


def generate_sample_context():
    """Generate sample context for template preview"""
    return {
        "contract": {
            "name": "CNT-2026-00001",
            "contract_number": "CNT-2026-00001",
            "contract_date": "2026-01-03",
            "valid_until": "2026-07-03",
            "total_amount": 5000000
        },
        "customer": {
            "customer_name": "Иван Иванов",
            "phone": "+998 90 123-45-67",
            "passport_series": "AA",
            "passport_number": "1234567",
            "address": "г. Ташкент, ул. Примерная, д. 1"
        },
        "merchant": {
            "company_name": "Nasiya365",
            "logo": "",
            "phone": "+998 71 123-45-67"
        },
        "installment_plan": {
            "principal_amount": 5000000,
            "total_amount": 5300000,
            "down_payment": 1000000,
            "number_of_installments": 6,
            "installment_amount": 716667,
            "schedule": [
                {"installment_number": 1, "due_date": "2026-02-03", "amount": 716667},
                {"installment_number": 2, "due_date": "2026-03-03", "amount": 716667},
                {"installment_number": 3, "due_date": "2026-04-03", "amount": 716667},
                {"installment_number": 4, "due_date": "2026-05-03", "amount": 716667},
                {"installment_number": 5, "due_date": "2026-06-03", "amount": 716667},
                {"installment_number": 6, "due_date": "2026-07-03", "amount": 716665}
            ]
        },
        "items": [
            {"product_name": "Телевизор Samsung 55\"", "quantity": 1, "unit_price": 5000000, "amount": 5000000}
        ],
        "today": "2026-01-03"
    }
