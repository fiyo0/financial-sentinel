"""
services/identity_service.py - Unified Identity & Key Resolution Service.
Handles tenant user resolution across web sessions, API tokens, and Telegram handles.
"""
from typing import Optional, Dict, Any
from storage.state_store import StateStore
from auth.crypto import verify_session_token
from config import config


class IdentityService:
    def __init__(self, state_store: StateStore):
        self.state_store = state_store

    def resolve_user_from_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Resolves authenticated user from encrypted session token."""
        if not token:
            return None
        payload = verify_session_token(token)
        if payload and payload.get("uid"):
            return self.state_store.get_user_by_id(payload["uid"])
        return None

    def resolve_user_from_telegram(
        self, chat_id: Optional[str] = None, username: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Resolves user from Telegram chat_id or @username with admin fallback."""
        user = None
        if chat_id:
            user = self.state_store.get_user_by_telegram(chat_id)
        if not user and username:
            user = self.state_store.get_user_by_telegram(username)

        # Admin fallback matching configured usernames or chat IDs
        if not user:
            allowed_users = [u.lower().replace("@", "") for u in config.telegram_allowed_usernames if u]
            allowed_chats = [c for c in config.telegram_allowed_chat_ids if c]
            if (username and username.lower() in allowed_users) or (chat_id and chat_id in allowed_chats):
                user = self.state_store.get_or_create_default_admin()
        return user

    def resolve_api_key(self, user_id: Optional[str] = None) -> str:
        """Resolves active Gemini API key (BYOK key prioritized over server default)."""
        from auth.crypto import decrypt_api_key

        if not user_id:
            admin = self.state_store.get_or_create_default_admin()
            if admin and admin.get("encrypted_gemini_key"):
                return decrypt_api_key(admin["encrypted_gemini_key"])
            return config.gemini_api_key

        user = self.state_store.get_user_by_id(user_id)
        if not user:
            return ""

        if user.get("encrypted_gemini_key"):
            dec = decrypt_api_key(user["encrypted_gemini_key"])
            if dec:
                return dec

        # Check if user is admin or matches allowed admin telegram identifiers
        allowed_tg = [u.lower().replace("@", "") for u in config.telegram_allowed_usernames if u]
        user_tg = (user.get("telegram_username") or "").lower().replace("@", "")
        user_chat = str(user.get("telegram_chat_id") or "").strip()
        user_name = (user.get("username") or "").lower().strip()
        is_allowed_admin = (
            user.get("role") == "admin"
            or (user_tg and user_tg in allowed_tg)
            or (user_chat and user_chat in config.telegram_allowed_chat_ids)
            or (user_name and user_name in allowed_tg)
        )

        if is_allowed_admin:
            return config.gemini_api_key

        return ""

    def link_telegram_chat_id(self, user_id: str, chat_id: str) -> None:
        """Persists linked Telegram chat_id for a user."""
        if user_id and chat_id:
            self.state_store.update_user_settings(user_id, telegram_chat_id=chat_id)
