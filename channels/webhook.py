"""
Generic Webhook Notification Channel Adapter.
Dispatches structured JSON payloads to automation tools like Zapier, n8n, Make.com, or custom microservices.
"""
import httpx
from typing import Optional, Dict, Any
from config import config


class GenericWebhookChannel:
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or config.generic_webhook_url

    def is_configured(self) -> bool:
        return bool(self.webhook_url and self.webhook_url.startswith("http"))

    def send_payload(self, event_type: str, payload: Dict[str, Any]) -> bool:
        if not self.is_configured():
            return False

        data = {
            "event": event_type,
            "data": payload
        }
        try:
            resp = httpx.post(self.webhook_url, json=data, timeout=8.0)
            return resp.status_code in (200, 201, 202, 204)
        except Exception:
            return False
