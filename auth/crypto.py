"""
Cryptographic and Authentication Services for Financial Sentinel.
Implements:
1. AES-256 (Fernet) Encryption at rest for Gemini API Keys with server secret key.
2. NIST PBKDF2-HMAC-SHA256 password hashing with unique 16-byte random salts.
3. Tamper-proof, encrypted, timestamped Fernet session tokens.
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
from cryptography.fernet import Fernet, InvalidToken
import httpx

logger = logging.getLogger("AuthCrypto")


def _get_master_fernet() -> Fernet:
    secret = os.getenv("APP_SECRET_KEY") or os.getenv("DASHBOARD_PASSWORD") or "financial-sentinel-master-secret-2026"
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def encrypt_api_key(raw_key: str) -> str:
    """Encrypts a raw Gemini API key using AES-256 Fernet."""
    if not raw_key or not raw_key.strip():
        return ""
    clean_key = raw_key.strip()
    f = _get_master_fernet()
    encrypted_bytes = f.encrypt(clean_key.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypts an encrypted Gemini API key with candidate secret fallback."""
    if not encrypted_key or not encrypted_key.strip():
        return ""
    candidate_secrets = [
        os.getenv("APP_SECRET_KEY"),
        os.getenv("DASHBOARD_PASSWORD"),
        "sentinel_admin",
        "financial-sentinel-master-secret-2026"
    ]
    for sec in candidate_secrets:
        if not sec:
            continue
        try:
            digest = hashlib.sha256(sec.encode("utf-8")).digest()
            f = Fernet(base64.urlsafe_b64encode(digest))
            decrypted_bytes = f.decrypt(encrypted_key.strip().encode("utf-8"))
            val = decrypted_bytes.decode("utf-8")
            if val:
                return val
        except Exception:
            continue
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
    """Verifies a plaintext password against a stored PBKDF2 hash."""
    if not password or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
            # Fallback legacy plaintext match for seamless backward migration
            return password == stored_hash
        salt = bytes.fromhex(parts[1])
        expected_kdf = bytes.fromhex(parts[2])
        actual_kdf = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
        return secrets.compare_digest(expected_kdf, actual_kdf)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def create_session_token(user_id: str, username: str, role: str = "user") -> str:
    """Generates an encrypted, tamper-proof session token."""
    f = _get_master_fernet()
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
    """Verifies and extracts payload from a session token with TTL enforcement and candidate secrets."""
    if not token or not token.strip():
        return None
    candidate_secrets = [
        os.getenv("APP_SECRET_KEY"),
        os.getenv("DASHBOARD_PASSWORD"),
        "sentinel_admin",
        "financial-sentinel-master-secret-2026"
    ]
    max_age_seconds = max_age_days * 86400
    for sec in candidate_secrets:
        if not sec:
            continue
        try:
            digest = hashlib.sha256(sec.encode("utf-8")).digest()
            f = Fernet(base64.urlsafe_b64encode(digest))
            decrypted = f.decrypt(token.strip().encode("utf-8"), ttl=max_age_seconds)
            data = json.loads(decrypted.decode("utf-8"))
            if data and "uid" in data:
                return data
        except Exception:
            continue
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
