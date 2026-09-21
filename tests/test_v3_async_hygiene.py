import asyncio
import threading
from starlette.requests import Request
from web.auth_deps import get_current_user_optional, require_user
from auth.crypto import create_session_token


def test_auth_deps_user_lookup_runs_in_worker_thread(monkeypatch):
    """Verify that get_current_user_optional uses asyncio.to_thread to run SQLite lookups off the main thread."""
    thread_ids = []

    class MockStateStore:
        def get_user_by_id(self, uid):
            thread_ids.append(threading.get_ident())
            return {"id": uid, "username": "trader1", "role": "user", "token_epoch": 1}

    token = create_session_token("user_123", "trader1", role="user", epoch=1)
    scope = {
        "type": "http",
        "headers": [(b"x-sentinel-token", token.encode("utf-8"))],
        "client": ("127.0.0.1", 80),
    }
    req = Request(scope)

    from web import auth_deps
    monkeypatch.setattr(auth_deps, "get_state_store", lambda r: MockStateStore())

    async def _run():
        main_tid = threading.get_ident()
        user = await get_current_user_optional(req)
        assert user is not None
        assert user["id"] == "user_123"
        assert len(thread_ids) == 1
        # The database query MUST have run on a worker thread, NOT on the main event loop thread
        assert thread_ids[0] != main_tid

    asyncio.run(_run())


def test_require_user_asynchronously_authenticates(monkeypatch):
    """Verify require_user dependency works asynchronously and enforces authentication."""
    class MockStateStore:
        def get_user_by_id(self, uid):
            return {"id": uid, "username": "admin_user", "role": "admin", "token_epoch": 1}

    token = create_session_token("admin_999", "admin_user", role="admin", epoch=1)
    scope = {
        "type": "http",
        "headers": [(b"x-sentinel-token", token.encode("utf-8"))],
        "client": ("127.0.0.1", 80),
    }
    req = Request(scope)

    from web import auth_deps
    monkeypatch.setattr(auth_deps, "get_state_store", lambda r: MockStateStore())

    async def _run():
        user = await require_user(req)
        assert user["id"] == "admin_999"
        assert user["role"] == "admin"

    asyncio.run(_run())
