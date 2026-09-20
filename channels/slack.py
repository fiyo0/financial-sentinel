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

    @staticmethod
    def _validate_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enforces Slack Block Kit constraints:
        1. Maximum 50 blocks per payload.
        2. Maximum 2900 characters per text object within any block.
        """
        if not blocks:
            return []
        valid_blocks = []
        for blk in blocks[:50]:
            clean_blk = dict(blk)
            if "text" in clean_blk and isinstance(clean_blk["text"], dict):
                t_obj = dict(clean_blk["text"])
                if "text" in t_obj and isinstance(t_obj["text"], str):
                    if len(t_obj["text"]) > 2900:
                        t_obj["text"] = t_obj["text"][:2897] + "..."
                clean_blk["text"] = t_obj
            elif "text" in clean_blk and isinstance(clean_blk["text"], str):
                if len(clean_blk["text"]) > 2900:
                    clean_blk["text"] = clean_blk["text"][:2897] + "..."
            valid_blocks.append(clean_blk)
        return valid_blocks

    def send_blocks(self, text_fallback: str, blocks: List[Dict[str, Any]]) -> bool:
        if not self.is_configured():
            return False

        validated_blocks = self._validate_blocks(blocks)
        fallback = text_fallback[:2900] if len(text_fallback) > 2900 else text_fallback
        payload = {
            "text": fallback,
            "blocks": validated_blocks
        }
        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=8.0)
            if resp.status_code != 200:
                logger.error("Slack webhook returned %d: %s", resp.status_code, resp.text)
                return False
            return True
        except httpx.HTTPError as e:
            logger.error("Failed sending Slack notification: %s", e)
            return False
