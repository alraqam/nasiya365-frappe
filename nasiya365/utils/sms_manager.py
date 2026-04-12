"""
SMS Manager for Nasiya365.
Supports Eskiz (Uzbekistan) and Playmobile providers.
"""

import uuid

import frappe
import requests

_ESKIZ_AUTH_URL = "https://notify.eskiz.uz/api/auth/login"
_ESKIZ_REFRESH_URL = "https://notify.eskiz.uz/api/auth/refresh"
_ESKIZ_SEND_URL = "https://notify.eskiz.uz/api/message/sms/send"
_PLAYMOBILE_SEND_URL = "http://91.204.239.44/broker-api/send"

_TOKEN_CACHE_KEY = "nasiya365_eskiz_token"
_TOKEN_TTL = 25 * 24 * 3600  # 25 days (token valid 30 days)


class SMSManager:
    def __init__(self):
        try:
            self.settings = frappe.get_single("SMS Gateway Settings")
            self.provider = (self.settings.sms_provider or "").strip()
            self.sender_id = (self.settings.sender_id or "4546").strip()
        except Exception:
            self.provider = None
            self.sender_id = "4546"

    def send_sms(self, phone_number, message):
        """Dispatch SMS via configured provider. Returns True on success."""
        if not self.provider:
            frappe.log_error("SMS provider not configured", "SMS Manager")
            return False
        if not phone_number:
            return False

        phone = _normalize_phone(phone_number)
        try:
            if self.provider == "Eskiz":
                return self._send_eskiz(phone, message)
            elif self.provider == "Playmobile":
                return self._send_playmobile(phone, message)
            else:
                frappe.log_error(f"Unknown SMS provider: {self.provider}", "SMS Manager")
                return False
        except Exception:
            frappe.log_error(frappe.get_traceback(), "SMS Manager")
            return False

    # ------------------------------------------------------------------
    # Eskiz
    # ------------------------------------------------------------------

    def _eskiz_token(self, force_refresh=False):
        """Return a valid Eskiz token, re-authenticating if needed."""
        if not force_refresh:
            cached = frappe.cache().get_value(_TOKEN_CACHE_KEY)
            if cached:
                return cached

        email = self.settings.eskiz_email
        password = self.settings.get_password("eskiz_api_key")
        if not email or not password:
            frappe.log_error("Eskiz credentials not set", "SMS Manager")
            return None

        try:
            resp = requests.post(
                _ESKIZ_AUTH_URL,
                data={"email": email, "password": password},
                timeout=10,
            )
            data = resp.json()
            if resp.status_code != 200 or not data.get("data", {}).get("token"):
                frappe.log_error(
                    f"Eskiz auth failed ({resp.status_code}): {data.get('message')}",
                    "SMS Manager",
                )
                return None
            token = data["data"]["token"]
            frappe.cache().set_value(_TOKEN_CACHE_KEY, token, expires_in_sec=_TOKEN_TTL)
            return token
        except Exception:
            frappe.log_error(frappe.get_traceback(), "SMS Manager — Eskiz auth")
            return None

    def _send_eskiz(self, phone, message, _retry=True):
        token = self._eskiz_token()
        if not token:
            return False

        callback_url = frappe.utils.get_url(
            "/api/method/nasiya365.callbacks.sms_delivery_status"
        )
        payload = {
            "mobile_phone": phone,
            "message": message,
            "from": self.sender_id,
            "callback_url": callback_url,
        }
        try:
            resp = requests.post(
                _ESKIZ_SEND_URL,
                headers={"Authorization": f"Bearer {token}"},
                data=payload,
                timeout=10,
            )
            if resp.status_code == 401 and _retry:
                # Token expired — force re-auth and retry once
                frappe.cache().delete_value(_TOKEN_CACHE_KEY)
                return self._send_eskiz(phone, message, _retry=False)

            result = resp.json()
            if resp.status_code not in (200, 201):
                frappe.log_error(
                    f"Eskiz send failed ({resp.status_code}): {result}",
                    "SMS Manager",
                )
                return False
            return True
        except Exception:
            frappe.log_error(frappe.get_traceback(), "SMS Manager — Eskiz send")
            return False

    # ------------------------------------------------------------------
    # Playmobile
    # ------------------------------------------------------------------

    def _send_playmobile(self, phone, message):
        import base64

        username = self.settings.playmobile_username
        password = self.settings.get_password("playmobile_password")
        if not username or not password:
            frappe.log_error("Playmobile credentials not set", "SMS Manager")
            return False

        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        msg_id = str(uuid.uuid4())[:20]
        payload = {
            "messages": [
                {
                    "recipient": phone,
                    "message-id": msg_id,
                    "sms": {
                        "originator": self.sender_id,
                        "content": {"text": message},
                    },
                }
            ]
        }
        try:
            resp = requests.post(
                _PLAYMOBILE_SEND_URL,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            if resp.status_code not in (200, 201, 202):
                frappe.log_error(
                    f"Playmobile send failed ({resp.status_code}): {resp.text}",
                    "SMS Manager",
                )
                return False
            return True
        except Exception:
            frappe.log_error(frappe.get_traceback(), "SMS Manager — Playmobile send")
            return False


def _normalize_phone(phone):
    """Strip spaces/dashes; ensure no leading +."""
    return phone.replace("+", "").replace(" ", "").replace("-", "").strip()
