"""
Telegram Notification Channel Adapter.
Sends formatted HTML alerts and digests to Telegram chats with automatic error recovery and message chunking.
"""
import re
import logging
import time
import httpx
from typing import Optional, List
from config import config

logger = logging.getLogger("TelegramChannel")


class TelegramChannel:
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or config.telegram_bot_token
        self.chat_id = chat_id or config.telegram_chat_id

    def get_effective_chat_id(self) -> Optional[str]:
        if self.chat_id:
            return self.chat_id
        if config.telegram_chat_id:
            return config.telegram_chat_id

        import os
        chat_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "telegram_chat_id.txt")
        if os.path.exists(chat_file):
            try:
                with open(chat_file, "r") as f:
                    cid = f.read().strip()
                    if cid:
                        self.chat_id = cid
                        config.telegram_chat_id = cid
                        return cid
            except Exception as e:
                logger.debug("Failed reading telegram_chat_id.txt: %s", e)

        try:
            from storage.state_store import StateStore
            store = StateStore()
            saved_cid = store.get_kv("last_known_telegram_chat_id")
            if saved_cid:
                self.chat_id = str(saved_cid)
                config.telegram_chat_id = str(saved_cid)
                return self.chat_id
            admin = store.get_or_create_default_admin()
            if admin and admin.get("telegram_chat_id"):
                cid = str(admin["telegram_chat_id"]).strip()
                self.chat_id = cid
                config.telegram_chat_id = cid
                return cid
            users = store.get_all_active_telegram_users()
            if users and users[0].get("telegram_chat_id"):
                cid = str(users[0]["telegram_chat_id"]).strip()
                self.chat_id = cid
                config.telegram_chat_id = cid
                return cid
        except Exception as e:
            logger.debug("Failed resolving telegram chat ID from state store: %s", e)

        return None

    def persist_chat_id(self, chat_id: str):
        if not chat_id:
            return
        self.chat_id = str(chat_id).strip()
        config.telegram_chat_id = self.chat_id
        import os
        chat_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "telegram_chat_id.txt")
        os.makedirs(os.path.dirname(chat_file), exist_ok=True)
        try:
            with open(chat_file, "w") as f:
                f.write(self.chat_id)
        except Exception as e:
            logger.warning("Failed persisting chat_id to file: %s", e)
        try:
            from storage.state_store import StateStore
            StateStore().set_kv("last_known_telegram_chat_id", self.chat_id)
        except Exception as e:
            logger.warning("Failed persisting chat_id to StateStore KV: %s", e)


    def is_configured(self) -> bool:
        cid = self.get_effective_chat_id()
        return bool(self.bot_token and cid)

    @staticmethod
    def _split_long_line(line: str, max_length: int = 4000) -> List[str]:
        if len(line) <= max_length:
            return [line]
        parts: List[str] = []
        sentence_endings = re.split(r'([.!?]\s+)', line)
        tokens: List[str] = []
        i = 0
        while i < len(sentence_endings):
            tok = sentence_endings[i]
            if i + 1 < len(sentence_endings):
                tok += sentence_endings[i + 1]
                i += 2
            else:
                i += 1
            tokens.append(tok)

        current = ""
        for tok in tokens:
            if len(tok) > max_length:
                words = tok.split(" ")
                w_current = ""
                for w in words:
                    if len(w) > max_length:
                        if w_current:
                            parts.append(w_current)
                            w_current = ""
                        for j in range(0, len(w), max_length):
                            parts.append(w[j:j + max_length])
                    elif len(w_current) + len(w) + 1 > max_length:
                        if w_current:
                            parts.append(w_current)
                        w_current = w
                    else:
                        w_current = f"{w_current} {w}" if w_current else w
                if w_current:
                    parts.append(w_current)
            elif len(current) + len(tok) > max_length:
                if current:
                    parts.append(current)
                current = tok
            else:
                current += tok

        if current:
            parts.append(current)
        return parts if parts else [line[:max_length], line[max_length:]]

    def _split_message(self, text: str, max_length: int = 4000) -> List[str]:
        if len(text) <= max_length:
            return [text]
        raw_lines = text.split("\n")
        expanded_lines: List[str] = []
        for rl in raw_lines:
            if len(rl) > max_length:
                expanded_lines.extend(self._split_long_line(rl, max_length))
            else:
                expanded_lines.append(rl)

        chunks: List[str] = []
        current_chunk = ""
        for line in expanded_lines:
            if len(current_chunk) + len(line) + 1 > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk = f"{current_chunk}\n{line}" if current_chunk else line
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def send_message(self, text: str, chat_id: Optional[str] = None) -> bool:
        cid = chat_id or self.get_effective_chat_id()
        tok = self.bot_token or config.telegram_bot_token
        if not tok or not cid:
            logger.warning("Telegram send failed: bot token or chat ID missing.")
            return False

        chunks = self._split_message(text, max_length=4000)
        success = True

        for chunk in chunks:
            url = f"https://api.telegram.org/bot{tok}/sendMessage"
            payload = {
                "chat_id": cid,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            try:
                resp = httpx.post(url, json=payload, timeout=12.0)
                if resp.status_code == 429:
                    retry_after = 1.0
                    try:
                        retry_after = float(resp.headers.get("Retry-After", 1.0))
                    except (ValueError, TypeError):
                        pass
                    logger.warning("Telegram rate limited (429). Backing off for %.1f seconds...", retry_after)
                    time.sleep(min(retry_after, 10.0))
                    resp = httpx.post(url, json=payload, timeout=12.0)

                if resp.status_code == 400:
                    logger.warning(f"Telegram HTML send returned status 400 ({resp.text}). Retrying with plain text...")
                    plain_text = re.sub(r'<[^>]+>', '', chunk)
                    payload_plain = {
                        "chat_id": cid,
                        "text": plain_text,
                        "disable_web_page_preview": True
                    }
                    resp_retry = httpx.post(url, json=payload_plain, timeout=12.0)
                    if resp_retry.status_code != 200:
                        logger.error(f"Telegram plain-text fallback also failed: {resp_retry.text}")
                        success = False
                elif resp.status_code != 200:
                    logger.error(f"Telegram send failed with status {resp.status_code}: {resp.text}")
                    success = False
            except Exception as e:
                logger.error(f"Telegram post exception: {e}")
                # Emergency plain-text retry
                try:
                    plain_text = re.sub(r'<[^>]+>', '', chunk)
                    resp_emerg = httpx.post(url, json={"chat_id": cid, "text": plain_text}, timeout=10.0)
                    if resp_emerg.status_code != 200:
                        success = False
                except (httpx.HTTPError, OSError) as err:
                    logger.error("Telegram emergency plain-text retry failed: %s", err)
                    success = False

        return success
