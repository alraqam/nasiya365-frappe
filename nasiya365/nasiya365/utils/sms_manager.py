import frappe
import requests
import json

class SMSManager:
    def __init__(self):
        try:
            self.settings = frappe.get_single("SMS Gateway Settings")
            self.provider = self.settings.sms_provider
        except Exception:
            self.provider = None

    def send_sms(self, phone_number, message):
        """Standardized method to dispatch SMS based on selected provider"""
        if not self.provider:
            return False

        try:
            if self.provider == "Eskiz":
                return self._send_eskiz(phone_number, message)
            elif self.provider == "Playmobile":
                return self._send_playmobile(phone_number, message)
            else:
                frappe.log_error("No valid SMS Provider configured", "SMS Manager")
                return False
        except Exception as e:
            frappe.log_error(f"SMS Failed: {str(e)}", "SMS Manager")
            return False

    def _send_eskiz(self, phone, message):
        email = self.settings.eskiz_email
        password = self.settings.get_password("eskiz_api_key")
        
        # 1. Get Token (ideally cache this)
        token = frappe.cache().get_value("eskiz_token")
        if not token:
            try:
                response = requests.post("https://notify.eskiz.uz/api/auth/login", data={"email": email, "password": password})
                data = response.json()
                if response.status_code != 200:
                    frappe.log_error(f"Eskiz Auth Failed: {data.get('message')}", "SMS Manager")
                    return False
                token = data['data']['token']
                # Token lasts 30 days usually, set for 29 days
                frappe.cache().set_value("eskiz_token", token, expires_in_sec=2500000) 
            except Exception as e:
                frappe.log_error(f"Eskiz Connection Error: {str(e)}", "SMS Manager")
                return False

        # 2. Send SMS
        headers = {"Authorization": f"Bearer {token}"}
        # Eskiz format usually requires stripping the plus sign if present, or specific formatting
        clean_phone = phone.replace("+", "").replace(" ", "")
        
        payload = {
            "mobile_phone": clean_phone, 
            "message": message,
            "from": "4546", # This should ideally be self.settings.sender_id
            "callback_url": frappe.utils.get_url("/api/method/nasiya365.callbacks.sms_status")
        }
        
        try:
            res = requests.post("https://notify.eskiz.uz/api/message/sms/send", headers=headers, data=payload)
            return res.json()
        except Exception as e:
            frappe.log_error(f"Eskiz Send Error: {str(e)}", "SMS Manager")
            return False

    def _send_playmobile(self, phone, message):
        # Placeholder for Playmobile implementation
        # You would implement similar logic here: Auth -> Send
        frappe.log_error("Playmobile integration not yet implemented", "SMS Manager")
        return False
