"""
Cryptographic and Authentication Services for Financial Sentinel.
Implements:
1. Fernet (AES-128-CBC with HMAC-SHA256 authenticated encryption) at rest for Gemini API Keys with HKDF-SHA256 key derivation.
2. NIST PBKDF2-HMAC-SHA256 password hashing with unique 16-byte random salts (120,000 rounds).
3. Tamper-proof, encrypted, timestamped Fernet session tokens with domain-separated HKDF keys.
4. Google AI Studio live API key verification test.
"""
import os
import json
import base64
import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet
import httpx

logger = logging.getLogger("AuthCrypto")


def _get_app_secret() -> str:
    secret = os.getenv("APP_SECRET_KEY")
    if not secret:
        if os.getenv("ALLOW_INSECURE_LOCAL") == "1" or os.getenv("PYTEST_CURRENT_TEST"):
            return "test-insecure-local-dev-secret-key-32b"
        raise RuntimeError(
            "CRITICAL SECURITY CONFIGURATION ERROR: APP_SECRET_KEY is not set in environment. "
            "Refusing to start with insecure or default keys."
        )
    return secret.strip()


def _get_fernet_for_context(context_info: bytes) -> Fernet:
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    secret = _get_app_secret()
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"financial-sentinel-v2-hkdf-salt",
        info=context_info,
    )
    key_bytes = hkdf.derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def _get_legacy_fernet() -> Optional[Fernet]:
    """Fallback legacy Fernet derived strictly from APP_SECRET_KEY for transparent migration."""
    secret = os.getenv("APP_SECRET_KEY")
    if not secret:
        if os.getenv("ALLOW_INSECURE_LOCAL") == "1" or os.getenv("PYTEST_CURRENT_TEST"):
            secret = "test-insecure-local-dev-secret-key-32b"
        else:
            return None
    digest = hashlib.sha256(secret.strip().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(raw_key: str) -> str:
    """Encrypts a raw Gemini API key using Fernet (AES-128-CBC + HMAC-SHA256) with HKDF key derivation."""
    if not raw_key or not raw_key.strip():
        return ""
    clean_key = raw_key.strip()
    f = _get_fernet_for_context(b"financial-sentinel-byok-encryption")
    encrypted_bytes = f.encrypt(clean_key.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypts an encrypted Gemini API key using HKDF-derived key with single APP_SECRET_KEY legacy fallback."""
    if not encrypted_key or not encrypted_key.strip():
        return ""
    clean_enc = encrypted_key.strip().encode("utf-8")

    # 1. Primary: HKDF-derived BYOK Fernet
    try:
        f = _get_fernet_for_context(b"financial-sentinel-byok-encryption")
        return f.decrypt(clean_enc).decode("utf-8")
    except Exception:
        pass

    # 2. Migration fallback: direct SHA256 of APP_SECRET_KEY (strictly no hardcoded secrets)
    try:
        f_leg = _get_legacy_fernet()
        if f_leg:
            return f_leg.decrypt(clean_enc).decode("utf-8")
    except Exception:
        pass

    return ""



def mask_api_key(raw_key: str) -> str:
    """Returns a safe masked version of an API key (e.g., AIzaSy...4x8F)."""
    if not raw_key or not raw_key.strip():
        return ""
    clean = raw_key.strip()
    if len(clean) <= 8:
        return "********"
    return f"{clean[:6]}...{clean[-4:]}"


def hash_password(password: str) -> str:
    """Hashes a password with PBKDF2-HMAC-SHA256 and a 16-byte random salt."""
    if not password:
        raise ValueError("Password cannot be empty")
    salt = secrets.token_bytes(16)
    kdf = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return f"pbkdf2_sha256${salt.hex()}${kdf.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a plaintext password against a stored PBKDF2 hash (no plaintext fallback)."""
    if not password or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
            logger.warning("Rejected non-PBKDF2 password hash format.")
            return False
        salt = bytes.fromhex(parts[1])
        expected_kdf = bytes.fromhex(parts[2])
        actual_kdf = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
        return secrets.compare_digest(expected_kdf, actual_kdf)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def create_session_token(user_id: str, username: str, role: str = "user") -> str:
    """Generates an encrypted, tamper-proof session token using HKDF-derived session Fernet."""
    f = _get_fernet_for_context(b"financial-sentinel-session-token")
    payload = {
        "uid": user_id,
        "usr": username,
        "rol": role,
        "iat": datetime.now(timezone.utc).timestamp()
    }
    raw_json = json.dumps(payload)
    token = f.encrypt(raw_json.encode("utf-8")).decode("utf-8")
    return token


def verify_session_token(token: str, max_age_days: int = 30) -> Optional[Dict[str, Any]]:
    """Verifies and extracts payload from a session token with TTL enforcement and strict key derivation."""
    if not token or not token.strip():
        return None
    clean_token = token.strip().encode("utf-8")
    max_age_seconds = max_age_days * 86400

    # 1. Primary: HKDF-derived session Fernet
    try:
        f = _get_fernet_for_context(b"financial-sentinel-session-token")
        decrypted = f.decrypt(clean_token, ttl=max_age_seconds)
        data = json.loads(decrypted.decode("utf-8"))
        if data and "uid" in data:
            return data
    except Exception:
        pass

    # 2. Migration fallback: direct SHA256 of APP_SECRET_KEY (strictly no hardcoded secrets)
    try:
        f_leg = _get_legacy_fernet()
        if f_leg:
            decrypted = f_leg.decrypt(clean_token, ttl=max_age_seconds)
            data = json.loads(decrypted.decode("utf-8"))
            if data and "uid" in data:
                return data
    except Exception:
        pass

    return None



def validate_gemini_api_key(api_key: str) -> Dict[str, Any]:
    """
    Validates a Gemini API key by making a lightweight model discovery ping to Google AI Studio.
    """
    if not api_key or not api_key.strip():
        return {"valid": False, "error": "API Key is empty"}
    clean_key = api_key.strip()
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    headers = {"x-goog-api-key": clean_key}
    try:
        resp = httpx.get(url, headers=headers, timeout=6.0)
        if resp.status_code == 200:
            return {"valid": True, "error": None}
        elif resp.status_code == 400:
            return {"valid": False, "error": "Invalid API key or unauthorized."}
        elif resp.status_code == 403:
            return {"valid": False, "error": "API key permissions denied or expired."}
        else:
            return {"valid": False, "error": f"Google AI Studio returned status {resp.status_code}"}
    except Exception as e:
        # If network times out or fails, return informative error
        return {"valid": False, "error": f"Network check failed: {e}"}
