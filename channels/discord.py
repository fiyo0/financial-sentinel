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

        # Split description > 2000 chars across multiple embeds (up to Discord limit of 10)
        desc_chunks = []
        if len(description) <= 2000:
            desc_chunks = [description]
        else:
            for i in range(0, min(len(description), 20000), 2000):
                desc_chunks.append(description[i:i + 2000])

        embeds = []
        for idx, chunk in enumerate(desc_chunks[:10]):
            part_title = title[:250] if idx == 0 else f"{title[:230]} (Part {idx + 1})"
            embed_obj: Dict[str, Any] = {
                "title": part_title,
                "description": chunk,
                "color": color,
                "footer": {"text": footer}
            }
            if idx == 0 and fields:
                embed_obj["fields"] = fields[:25]
            embeds.append(embed_obj)

        payload = {"embeds": embeds}

        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=8.0)
            if resp.status_code not in (200, 204):
                logger.error("Discord webhook returned %d: %s", resp.status_code, resp.text)
                return False
            return True
        except httpx.HTTPError as e:
            logger.error("Failed sending Discord embed: %s", e)
            return False
