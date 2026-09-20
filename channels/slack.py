"""
Slack Notification Channel Adapter.
Sends Block Kit formatted cards to Slack Incoming Webhooks.
"""
import logging
import httpx
from typing import Optional, Dict, Any, List
from config import config

logger = logging.getLogger(__name__)


class SlackChannel:
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or config.slack_webhook_url

    def is_configured(self) -> bool:
        return bool(self.webhook_url and "hooks.slack.com" in self.webhook_url)

    def send_blocks(self, text_fallback: str, blocks: List[Dict[str, Any]]) -> bool:
        if not self.is_configured():
            return False

        payload = {
            "text": text_fallback,
            "blocks": blocks
        }
        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=8.0)
            return resp.status_code == 200
        except httpx.HTTPError as e:
            logger.error("Failed sending Slack notification: %s", e)
            return False
