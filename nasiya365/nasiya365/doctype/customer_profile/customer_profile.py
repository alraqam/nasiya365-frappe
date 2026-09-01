"""
Customer Profile DocType Controller
Handles customer management with enhanced validation for personal info, addresses, and identity documents
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today, date_diff
import re

# credit_limit <= 0 means no cap (default unlimited for new clients).
# Must fit Frappe Currency / MySQL DECIMAL(21,9) (max ~999_999_999_999.999999999).
_UNLIMITED_CREDIT_NOTIONAL = 999_999_999_999


class CustomerProfile(Document):
    def validate(self):
        self.set_full_name()
        self.validate_phone_numbers()
        self.validate_passport()
        self.validate_pinfl()
        self.validate_age()
        self.validate_passport_dates()
        self.sync_addresses()
        self.update_available_limit()
        self.calculate_risk_profile()

    def on_trash(self):
        """Block customer deletion if any plan or payment references them — otherwise
        FK pointers go stale and financial reconciliation breaks. Cancel + delete the
        related docs first."""
        ip_count = frappe.db.count("Installment Plan", {"customer": self.name})
        if ip_count:
            frappe.throw(
                _(
                    "Невозможно удалить клиента: к нему привязано {0} план(ов) рассрочки."
                ).format(ip_count)
            )
        pt_count = frappe.db.count("Payment Transaction", {"customer": self.name})
        if pt_count:
            frappe.throw(
                _(
                    "Невозможно удалить клиента: к нему привязано {0} платёж(ей)."
                ).format(pt_count)
            )
        so_count = frappe.db.count("Sales Order", {"customer": self.name})
        if so_count:
            frappe.throw(
                _(
                    "Невозможно удалить клиента: к нему привязано {0} заказ(ов) продажи."
                ).format(so_count)
            )
    
    def set_full_name(self):
        """Set full_name from first_name and last_name"""
        parts = [self.first_name or "", self.last_name or ""]
        self.full_name = " ".join(p for p in parts if p).strip()
        
    def validate_phone_numbers(self):
        """Validate phone numbers table - ensure at least one exists and only one is primary"""
        if not self.phone_numbers or len(self.phone_numbers) == 0:
            frappe.throw(_("At least one phone number is required"))
        
        # Count primary phones
        primary_count = sum(1 for phone in self.phone_numbers if phone.is_primary)
        
        if primary_count == 0:
            frappe.throw(_("Please mark one phone number as Primary/Main"))
        
        if primary_count > 1:
            frappe.throw(_("Only one phone number can be marked as Primary/Main"))
    
    def validate_passport(self):
        """Validate passport format"""
        if self.passport_series:
            self.passport_series = self.passport_series.upper()
            if len(self.passport_series) != 2:
                frappe.throw(_("Passport series must be 2 letters"))
        
        if self.passport_number:
            if not self.passport_number.isdigit() or len(self.passport_number) > 15:
                frappe.throw(_("Passport number must be digits only, max 15 characters"))
    
    def validate_pinfl(self):
        """Validate PINFL (14 digits)"""
        if self.pinfl:
            # Remove spaces and dashes
            clean_pinfl = re.sub(r'[\s\-]', '', self.pinfl)
            
            if not re.match(r'^[0-9]{14}$', clean_pinfl):
                frappe.throw(_("PINFL must be exactly 14 digits"))
            
            self.pinfl = clean_pinfl
    
    def validate_age(self):
        """Validate customer is at least 18 years old"""
        if self.date_of_birth:
            age = date_diff(today(), self.date_of_birth) / 365
            if age < 18:
                frappe.throw(_("Customer must be at least 18 years old"))
            if age > 65:
                frappe.msgprint(_("Customer is over 65 years old. Manual approval may be required."))
    
    def validate_passport_dates(self):
        """Validate passport issue and expiry dates"""
        if self.passport_issue_date and self.passport_expiry_date:
            if getdate(self.passport_expiry_date) <= getdate(self.passport_issue_date):
                frappe.throw(_("Passport expiry date must be after issue date"))
    
    def sync_addresses(self):
        """If 'same as registration' is checked, copy registration address to current address"""
        if self.same_as_registration:
            self.current_address = self.registration_address
    
    def get_primary_phone(self):
        """Get the primary phone number"""
        for phone in self.phone_numbers:
            if phone.is_primary:
                return phone.phone_number
        return None

    def update_available_limit(self):
        """Calculate available limit = credit_limit - total_active_debt (unlimited if credit_limit <= 0)."""
        from frappe.utils import flt

        limit = flt(self.credit_limit)
        debt = flt(self.total_debt)
        if limit <= 0:
            self.available_limit = max(flt(0), flt(_UNLIMITED_CREDIT_NOTIONAL) - debt)
        else:
            self.available_limit = limit - debt
        
    def _outstanding_principal_total(self) -> float:
        """Сумма непогашенного основного долга по живым проведённым договорам.

        Кредитная база — основной долг без будущих процентов: ставка это цена
        услуги, а не выданные деньги, и клиент со ставкой 2% в месяц на год не
        должен «съедать» лимит на 24% больше, чем стоил товар.
        """
        from frappe.utils import flt

        from nasiya365.finance import outstanding_principal

        plans = frappe.db.sql(
            """
            SELECT ip.name, ip.financed_amount, ip.total_interest, ip.paid_amount,
                   ip.down_payment,
                   EXISTS (SELECT 1 FROM `tabInstallment Schedule` s
                           WHERE s.parent = ip.name AND s.installment_number = 0) AS has_dp_row
            FROM `tabInstallment Plan` ip
            WHERE ip.customer = %s
              AND ip.docstatus = 1
              AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан', 'Отменен')
            """,
            (self.name,),
            as_dict=True,
        )
        return flt(sum(
            outstanding_principal(
                financed_amount=p.financed_amount,
                total_interest=p.total_interest,
                paid_amount=p.paid_amount,
                down_payment=p.down_payment,
                has_down_payment_row=bool(p.has_dp_row),
            )
            for p in plans
        ), 2)

    def update_statistics(self):
        """Update customer stats from Installment Plans — single SQL, no N+1."""
        from frappe.utils import flt

        # Долг считается НЕ в этом запросе, а отдельно, в Python: кредитная база —
        # непогашенный основной долг без будущих процентов (решение владельца), и
        # выражать её в SQL пришлось бы вместе с проверкой строки 0 графика.
        # Договоров у клиента единицы, поэтому цена такого чтения ничтожна.
        #
        # Агрегаты просрочки остаются на join'е: он им нужен и от дублирования они
        # не страдают — COUNT(DISTINCT) схлопывает повторы, MAX идемпотентен, а
        # overdue_count считает как раз строки графика.
        row = frappe.db.sql(
            """
            SELECT
                COUNT(DISTINCT ip.name)                                               AS active_contracts_count,
                COALESCE(MAX(
                    CASE WHEN isc.status = 'Просрочен'
                         THEN DATEDIFF(CURDATE(), isc.due_date)
                         ELSE 0 END
                ), 0)                                                                  AS max_delay,
                COALESCE(SUM(
                    CASE WHEN isc.status = 'Просрочен' THEN 1 ELSE 0 END
                ), 0)                                                                  AS overdue_count
            FROM `tabInstallment Plan` ip
            LEFT JOIN `tabInstallment Schedule` isc ON isc.parent = ip.name
            WHERE ip.customer = %(customer)s
              AND ip.docstatus = 1
              AND IFNULL(ip.status, '') NOT IN ('Завершен', 'Списан', 'Отменен')
            """,
            {"customer": self.name},
            as_dict=True,
        )
        stats = row[0] if row else {}

        self.active_contracts_count = int(stats.get("active_contracts_count") or 0)
        self.total_debt = self._outstanding_principal_total()
        self.update_available_limit()
        self.calculate_risk_profile(
            max_delay=int(stats.get("max_delay") or 0),
            overdue_count=int(stats.get("overdue_count") or 0),
        )
        self.db_update()

    def calculate_risk_profile(self, max_delay=None, overdue_count=None):
        """Compute risk score. Pass pre-computed values to skip the DB query (used by update_statistics)."""
        from frappe.utils import flt

        if max_delay is None or overdue_count is None:
            # Called from validate() — fetch from DB
            overdue_rows = frappe.db.sql(
                """
                SELECT
                    COALESCE(MAX(DATEDIFF(CURDATE(), isc.due_date)), 0) AS max_delay,
                    COUNT(*) AS overdue_count
                FROM `tabInstallment Schedule` isc
                INNER JOIN `tabInstallment Plan` ip ON ip.name = isc.parent
                WHERE ip.customer = %s
                  AND ip.docstatus = 1
                  AND isc.status = 'Просрочен'
                """,
                (self.name,),
                as_dict=True,
            )
            r = overdue_rows[0] if overdue_rows else {}
            max_delay = int(r.get("max_delay") or 0)
            overdue_count = int(r.get("overdue_count") or 0)

        utilization = 0.0
        if flt(self.credit_limit) > 0:
            utilization = (flt(self.total_debt) / flt(self.credit_limit)) * 100

        score = 100
        score -= min(max_delay, 45)
        score -= min(overdue_count * 8, 30)
        score -= min(int(utilization // 10) * 2, 20)
        score = max(0, min(100, score))

        self.last_payment_delay_days = max_delay
        self.risk_score = score
        self.risk_level = "Низкий" if score >= 70 else ("Средний" if score >= 40 else "Высокий")


@frappe.whitelist()
def get_customer_by_phone(phone):
    """API endpoint to find customer by phone number"""
    # Normalize phone format
    clean_phone = re.sub(r'[\s\-]', '', phone)
    
    # Search in Customer Phone Number child table
    phone_records = frappe.db.get_all("Customer Phone Number", 
        filters={
            "phone_number": ["in", [phone, f"+998{clean_phone}", clean_phone]]
        },
        fields=["parent"]
    )
    
    if phone_records:
        customer_name = phone_records[0].parent
        customer = frappe.get_doc("Customer Profile", customer_name)
        return {
            "name": customer.name,
            "first_name": customer.first_name,
            "last_name": customer.last_name,
            "phone": customer.get_primary_phone(),
            "status": customer.status
        }
    
    return None
