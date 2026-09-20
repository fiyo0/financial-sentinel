"""
Discord Notification Channel Adapter.
Sends rich embed cards with visual status colors to Discord channels.
"""
import logging
import httpx
from typing import Optional, Dict, Any, List
from config import config

logger = logging.getLogger(__name__)


class DiscordChannel:
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or config.discord_webhook_url

    def is_configured(self) -> bool:
        return bool(self.webhook_url and self.webhook_url.startswith("https://discord.com/api/webhooks/"))

    def send_embed(
        self,
        title: str,
        description: str,
        color: int = 0x3498DB,  # Blue default
        fields: Optional[List[Dict[str, Any]]] = None,
        footer: str = "Financial Sentinel Multi-Agent"
    ) -> bool:
        if not self.is_configured():
            return False

        embed = {
            "title": title[:250],
            "description": description[:2000],
            "color": color,
            "fields": fields or [],
            "footer": {"text": footer}
        }
        payload = {"embeds": [embed]}

        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=8.0)
            return resp.status_code in (200, 204)
        except httpx.HTTPError as e:
            logger.error("Failed sending Discord embed: %s", e)
            return False
