import pytest
from fastapi.testclient import TestClient

from auth.crypto import (
    encrypt_api_key, decrypt_api_key, mask_api_key,
    hash_password, verify_password, create_session_token, verify_session_token
)
from storage.state_store import StateStore
from orchestrator import FinancialSentinelOrchestrator
from models import PortfolioHolding
from web.app import app


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_sentinel.db")
    store = StateStore(db_file)
    return store, db_file


def test_crypto_primitives():
    raw_key = "AIzaSyD9876543210SampleTestKeyForSentinel"
    encrypted = encrypt_api_key(raw_key)
    assert encrypted != raw_key
    assert len(encrypted) > 20

    decrypted = decrypt_api_key(encrypted)
    assert decrypted == raw_key

    # Masking
    masked = mask_api_key(raw_key)
    assert masked.startswith("AIzaSy")
    assert masked.endswith("inel")
    assert "..." in masked

    # Password hashing
    password = "SuperSecretPassword123!"
    pw_hash = hash_password(password)
    assert pw_hash != password
    assert verify_password(password, pw_hash) is True
    assert verify_password("WrongPassword", pw_hash) is False

    # Session Tokens
    token = create_session_token("usr_123", "alice", role="user")
    payload = verify_session_token(token)
    assert payload is not None
    assert payload["uid"] == "usr_123"
    assert payload["usr"] == "alice"
    assert payload["rol"] == "user"


def test_user_management_and_isolation(test_db):
    store, db_file = test_db
    orch = FinancialSentinelOrchestrator(db_path=db_file)

    # 1. Admin seeding
    admin = store.get_or_create_default_admin()
    assert admin["role"] == "admin"
    assert admin["username"] == "forello0"
    p_admin = orch.get_active_portfolio(user_id=admin["id"])
    assert len(p_admin.holdings) >= 5

    # 2. Create User Alice (with BYOK key)
    alice = store.create_user(
        username="alice",
        email="alice@example.com",
        password="AlicePassword123",
        telegram_username="alice_invests",
        raw_gemini_key="AIzaSyAlicePersonalKey99",
        role="user"
    )
    assert alice["role"] == "user"
    assert alice["encrypted_gemini_key"] != "AIzaSyAlicePersonalKey99"
    assert decrypt_api_key(alice["encrypted_gemini_key"]) == "AIzaSyAlicePersonalKey99"

    # Alice initial portfolio is empty
    p_alice = orch.get_active_portfolio(user_id=alice["id"])
    assert len(p_alice.holdings) == 0

    # Add a holding for Alice
    p_alice.holdings.append(PortfolioHolding(
        ticker="NVDA", name="NVIDIA", shares=5.0, avg_price=120.0, current_price=125.0
    ))
    orch.persist_active_portfolio(p_alice, user_id=alice["id"])

    # 3. Create User Bob (without BYOK key)
    bob = store.create_user(
        username="bob",
        email="bob@example.com",
        password="BobPassword123",
        telegram_username="bob_trades",
        raw_gemini_key="",
        role="user"
    )
    p_bob = orch.get_active_portfolio(user_id=bob["id"])
    assert len(p_bob.holdings) == 0

    p_bob.holdings.append(PortfolioHolding(
        ticker="TSLA", name="Tesla", shares=10.0, avg_price=200.0, current_price=220.0
    ))
    orch.persist_active_portfolio(p_bob, user_id=bob["id"])

    # Verify strict portfolio isolation
    reloaded_alice = orch.get_active_portfolio(user_id=alice["id"])
    reloaded_bob = orch.get_active_portfolio(user_id=bob["id"])
    reloaded_admin = orch.get_active_portfolio(user_id=admin["id"])

    assert len(reloaded_alice.holdings) == 1
    assert reloaded_alice.holdings[0].ticker == "NVDA"

    assert len(reloaded_bob.holdings) == 1
    assert reloaded_bob.holdings[0].ticker == "TSLA"

    assert len(reloaded_admin.holdings) >= 5
    admin_tickers = [h.ticker for h in reloaded_admin.holdings]
    assert "SPY" in admin_tickers
    assert "BND" in admin_tickers

    # 4. Strict API Key Isolation Verification
    key_alice = orch.resolve_user_api_key(user_id=alice["id"])
    key_bob = orch.resolve_user_api_key(user_id=bob["id"])
    key_admin = orch.resolve_user_api_key(user_id=admin["id"])

    assert key_alice == "AIzaSyAlicePersonalKey99"
    assert key_bob == ""  # STRICT: Bob NEVER receives Admin key!
    assert len(key_admin) > 0  # Admin has access to admin key


def test_telegram_user_routing(test_db):
    store, db_file = test_db
    orch = FinancialSentinelOrchestrator(db_path=db_file)

    # Create User Charlie with telegram handle
    charlie = store.create_user(
        username="charlie",
        email="charlie@example.com",
        password="PassCharlie123",
        telegram_username="charlie_tg",
        raw_gemini_key="AIzaSyCharlieKey",
        role="user"
    )

    # Lookup by username with/without @
    found1 = store.get_user_by_telegram("@charlie_tg")
    found2 = store.get_user_by_telegram("charlie_tg")
    assert found1 is not None and found1["id"] == charlie["id"]
    assert found2 is not None and found2["id"] == charlie["id"]

    # Test unknown user
    unknown = store.get_user_by_telegram("random_unknown_user")
    assert unknown is None


def test_fastapi_web_endpoints(test_db, monkeypatch):
    store, db_file = test_db
    orch = FinancialSentinelOrchestrator(db_path=db_file)
    from web import app as web_module
    monkeypatch.setattr(web_module, "orchestrator", orch)

    client = TestClient(app)

    # 1. Register User Dave
    reg_resp = client.post("/api/auth/register", json={
        "username": "dave",
        "email": "dave@example.com",
        "password": "DavePassword123",
        "telegram_username": "dave_invests",
        "gemini_api_key": "AIzaSyDaveKey123"
    })
    assert reg_resp.status_code == 200
    data = reg_resp.json()
    assert data["status"] == "success"
    assert data["user"]["username"] == "dave"

    # Get session cookie from registration
    cookies = reg_resp.cookies

    # 2. Get User Me
    me_resp = client.get("/api/user/me", cookies=cookies)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["username"] == "dave"
    assert me_data["has_gemini_key"] is True
    assert "..." in me_data["masked_gemini_key"]

    # 3. Update User Settings (Update cash and Telegram)
    settings_resp = client.post("/api/user/settings", json={
        "telegram_username": "dave_official",
        "cash": 25000.50
    }, cookies=cookies)
    assert settings_resp.status_code == 200
    set_data = settings_resp.json()
    assert set_data["user"]["telegram_username"] == "dave_official"

    # Verify portfolio cash
    port_resp = client.get("/api/portfolio", cookies=cookies)
    assert port_resp.status_code == 200
    port_data = port_resp.json()
    assert port_data["portfolio"]["cash"] == 25000.50

    # 4. Update Password in Settings
    pwd_update_resp = client.post("/api/user/settings", json={
        "new_password": "DaveBrandNewPassword2026"
    }, cookies=cookies)
    assert pwd_update_resp.status_code == 200

    # Old password fails login
    bad_login = client.post("/api/auth/login", json={
        "username_or_email": "dave",
        "password": "DavePassword123"
    })
    assert bad_login.status_code == 401

    # New password succeeds
    good_login = client.post("/api/auth/login", json={
        "username_or_email": "dave",
        "password": "DaveBrandNewPassword2026"
    })
    assert good_login.status_code == 200
    assert "sentinel_token" in good_login.cookies

