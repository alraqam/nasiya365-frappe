"""
Print Template DocType Controller
Custom templates for PDF generation
"""

import frappe
from frappe.model.document import Document


class PrintTemplate(Document):
    def validate(self):
        self.validate_default()
    
    def validate_default(self):
        """Ensure only one default template per type and language"""
        if self.is_default:
            frappe.db.set_value(
                "Print Template",
                {
                    "template_type": self.template_type,
                    "language": self.language,
                    "name": ("!=", self.name)
                },
                "is_default",
                0
            )


@frappe.whitelist()
def get_default_template(template_type, language="uz"):
    """Get default template for a type and language"""
    template = frappe.db.get_value(
        "Print Template",
        {"template_type": template_type, "language": language, "is_default": 1},
        ["name", "header_html", "body_html", "footer_html", "css_styles"],
        as_dict=True
    )
    
    if not template:
        # Fallback to any template of this type
        template = frappe.db.get_value(
            "Print Template",
            {"template_type": template_type},
            ["name", "header_html", "body_html", "footer_html", "css_styles"],
            as_dict=True
        )
    
    return template
