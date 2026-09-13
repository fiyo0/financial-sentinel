"""
web/auth_deps.py - FastAPI Authentication & Authorization Dependencies for Financial Sentinel.
Enforces non-optional authenticated user context and role-based access control by contract.
"""
from typing import Dict, Any, Optional
import secrets
from fastapi import Request, HTTPException, status, Depends
from auth.crypto import verify_session_token
from config import config


def get_state_store(request: Request):
    """Retrieves state_store from active orchestrator or app.state."""
    try:
        import web.app as web_app
        if hasattr(web_app, "orchestrator") and web_app.orchestrator is not None:
            return web_app.orchestrator.state_store
    except (ImportError, AttributeError):
        pass
    if hasattr(request.app.state, "store") and request.app.state.store is not None:
        return request.app.state.store
    from orchestrator import FinancialSentinelOrchestrator
    return FinancialSentinelOrchestrator().state_store


def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    """
    Extracts and authenticates user from encrypted session cookie or token header.
    Returns None if unauthenticated or session expired.
    """
    store = get_state_store(request)

    # 1. Check Fernet session token cookie or header
    token = request.cookies.get("sentinel_token") or request.headers.get("X-Sentinel-Token")
    if not token:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()

    if token and store:
        payload = verify_session_token(token)
        if payload and payload.get("uid"):
            user = store.get_user_by_id(payload["uid"])
            if user:
                return user

    # 2. Check legacy password cookie / header for default admin
    cookie_auth = request.cookies.get("sentinel_auth")
    header_auth = request.headers.get("X-Sentinel-Auth")
    if config.dashboard_password and (cookie_auth == config.dashboard_password or header_auth == config.dashboard_password):
        if store:
            return store.get_or_create_default_admin()

    # 3. If auth is completely disabled in config (local dev only)
    if not config.dashboard_auth_enabled and store:
        return store.get_or_create_default_admin()

    return None


async def require_user(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency requiring a valid, authenticated user session.
    Raises 401 Unauthorized if missing, invalid, or expired.
    Guarantees user dict is non-null and user['id'] is valid.
    """
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: Please sign in to access this resource."
        )
    return user


async def require_admin(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """
    FastAPI dependency requiring administrator role privileges.
    Raises 403 Forbidden if user is authenticated but not an admin.
    """
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Administrator privileges required."
        )
    return user


async def require_cron_or_admin(request: Request) -> Optional[Dict[str, Any]]:
    """
    FastAPI dependency for scheduled cron endpoints.
    Allows either a valid X-Cron-Secret header or an active administrator session.
    """
    cron_hdr = request.headers.get("X-Cron-Secret", "")
    if config.cron_secret and cron_hdr and secrets.compare_digest(cron_hdr, config.cron_secret):
        return {"id": "cron_scheduler", "role": "admin", "username": "scheduler"}

    user = get_current_user_optional(request)
    if user and user.get("role") == "admin":
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: Endpoint requires valid X-Cron-Secret or administrator session."
    )
