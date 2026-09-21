"""
web/auth_deps.py - FastAPI Authentication & Authorization Dependencies for Financial Sentinel.
Enforces non-optional authenticated user context and role-based access control by contract.
"""
import asyncio
from typing import Dict, Any, Optional
import secrets
from fastapi import Request, HTTPException, status, Depends
from auth.crypto import verify_session_token
from config import config


import logging

logger = logging.getLogger(__name__)


def get_state_store(request: Request):
    """Retrieves state_store from active orchestrator or app.state."""
    try:
        import web.app as web_app
        if hasattr(web_app, "orchestrator") and web_app.orchestrator is not None:
            return web_app.orchestrator.state_store
    except (ImportError, AttributeError) as e:
        logger.debug("Could not resolve orchestrator from web.app: %s", e)
    if hasattr(request.app.state, "store") and request.app.state.store is not None:
        return request.app.state.store
    from orchestrator import FinancialSentinelOrchestrator
    return FinancialSentinelOrchestrator().state_store


def _check_dashboard_auth_disabled_in_prod():
    if not config.dashboard_auth_enabled:
        import os
        if os.getenv("K_SERVICE") or os.getenv("ENVIRONMENT") == "production":
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: DASHBOARD_AUTH_ENABLED cannot be disabled in production. "
                "Refusing to execute unauthenticated in production."
            )


async def _get_current_user_optional_async(request: Request) -> Optional[Dict[str, Any]]:
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
            user = await asyncio.to_thread(store.get_user_by_id, payload["uid"])
            if user:
                # Invalidate stale session tokens across credential rotations (R-1)
                token_epoch = int(payload.get("epoch", 1))
                user_epoch = int(user.get("token_epoch") or 1)
                if token_epoch == user_epoch:
                    return user

    # 2. If auth is completely disabled in config (local dev only, strictly rejected in production) (R-7)
    if not config.dashboard_auth_enabled and store:
        return await asyncio.to_thread(store.get_or_create_default_admin)

    return None


def get_current_user_optional(request: Request):
    """
    Extracts and authenticates user from encrypted session cookie or token header.
    Runs database lookups in a worker thread (asyncio.to_thread) to prevent blocking the event loop.
    Returns a coroutine that resolves to Optional[Dict[str, Any]].
    """
    _check_dashboard_auth_disabled_in_prod()
    return _get_current_user_optional_async(request)



async def require_user(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency requiring a valid, authenticated user session.
    Raises 401 Unauthorized if missing, invalid, or expired.
    Guarantees user dict is non-null and user['id'] is valid.
    """
    user = await get_current_user_optional(request)
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

    user = await get_current_user_optional(request)
    if user and user.get("role") == "admin":
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: Endpoint requires valid X-Cron-Secret or administrator session."
    )
