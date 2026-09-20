"""
Automated unit and integration tests for strict multi-tenant isolation
on historical deep dives and briefings.
"""
import pytest
from fastapi.testclient import TestClient
from web.app import app, orchestrator, create_session_token


@pytest.fixture
def test_users():
    store = orchestrator.state_store
    # Create User Alice
    try:
        alice = store.create_user("alice_tenant", "alice@example.com", "pass12345", role="user")
    except Exception:
        alice = store.get_user_by_username("alice_tenant")

    # Create User Bob
    try:
        bob = store.create_user("bob_tenant", "bob@example.com", "pass12345", role="user")
    except Exception:
        bob = store.get_user_by_username("bob_tenant")

    # Admin user
    admin = store.get_or_create_default_admin()
    return alice, bob, admin


def test_deepdive_strict_tenant_isolation(test_users):
    """Verifies that non-admin users cannot read or delete other users' archived deep dives."""
    alice, bob, admin = test_users
    store = orchestrator.state_store
    client = TestClient(app)

    # 1. Alice creates a deep dive
    dd_id = store.save_deepdive(
        user_id=alice["id"],
        ticker="NVDA",
        company_name="NVIDIA Corporation",
        current_price=135.0,
        verdict="BULLISH",
        conviction_score=92.0,
        technicals={"rsi_14": 55.0},
        sentiment={"sentiment_score": 80.0},
        analysis_text="Alice's proprietary NVDA analysis."
    )

    # 2. Alice views her own deep dive -> 200 OK
    alice_token = create_session_token(alice["id"], alice["username"], role="user")
    client.cookies.set("sentinel_token", alice_token)
    res_alice = client.get(f"/api/deepdives/{dd_id}")
    assert res_alice.status_code == 200
    assert res_alice.json()["deepdive"]["ticker"] == "NVDA"

    # 3. Bob attempts to view Alice's deep dive -> 404 Not Found (prevents ID enumeration oracle)
    bob_token = create_session_token(bob["id"], bob["username"], role="user")
    client.cookies.set("sentinel_token", bob_token)
    res_bob = client.get(f"/api/deepdives/{dd_id}")
    assert res_bob.status_code == 404
    assert "not found" in res_bob.json()["detail"].lower()

    # 4. Bob attempts to delete Alice's deep dive -> 404 Not Found
    res_bob_del = client.delete(f"/api/deepdives/{dd_id}")
    assert res_bob_del.status_code == 404


    # 5. Admin views Alice's deep dive -> 200 OK
    admin_token = create_session_token(admin["id"], admin["username"], role="admin")
    client.cookies.set("sentinel_token", admin_token)
    res_admin = client.get(f"/api/deepdives/{dd_id}")
    assert res_admin.status_code == 200

    # 6. Alice deletes her own deep dive -> 200 OK
    client.cookies.set("sentinel_token", alice_token)
    res_del = client.delete(f"/api/deepdives/{dd_id}")
    assert res_del.status_code == 200


def test_briefing_strict_tenant_isolation(test_users):
    """Verifies that private user briefings are restricted while public briefings remain shared."""
    alice, bob, admin = test_users
    store = orchestrator.state_store
    client = TestClient(app)

    # 1. Public scheduled briefing (user_id is None)
    public_rep_id = "rep_public_premarket"
    store.record_market_briefing(
        briefing_id=public_rep_id,
        slot="premarket",
        message="Public 6:30 AM pre-market macro wrap.",
        user_id=None
    )

    # Both Alice and Bob can view public briefing
    alice_token = create_session_token(alice["id"], alice["username"], role="user")
    bob_token = create_session_token(bob["id"], bob["username"], role="user")

    client.cookies.set("sentinel_token", alice_token)
    res = client.get(f"/api/briefings/{public_rep_id}")
    assert res.status_code == 200

    client.cookies.set("sentinel_token", bob_token)
    res = client.get(f"/api/briefings/{public_rep_id}")
    assert res.status_code == 200

    # 2. Alice's private on-demand briefing
    alice_rep_id = "rep_alice_private"
    store.record_market_briefing(
        briefing_id=alice_rep_id,
        slot="general",
        message="Alice's private custom scan.",
        user_id=alice["id"]
    )

    # Alice views her own briefing -> 200
    client.cookies.set("sentinel_token", alice_token)
    assert client.get(f"/api/briefings/{alice_rep_id}").status_code == 200

    # Bob attempts to view Alice's private briefing -> 403 Forbidden
    client.cookies.set("sentinel_token", bob_token)
    res_bob = client.get(f"/api/briefings/{alice_rep_id}")
    assert res_bob.status_code == 403
    assert "Forbidden" in res_bob.json()["detail"]

    # Bob attempts to dispatch Alice's private briefing -> 403 Forbidden
    res_bob_disp = client.post(f"/api/briefings/{alice_rep_id}/dispatch")
    assert res_bob_disp.status_code == 403

    # Non-admin Bob attempts to prune briefings -> 403 Forbidden
    res_prune = client.post("/api/briefings/prune")
    assert res_prune.status_code == 403

    # Admin prunes briefings -> 200 OK
    admin_token = create_session_token(admin["id"], admin["username"], role="admin")
    client.cookies.set("sentinel_token", admin_token)
    assert client.post("/api/briefings/prune").status_code == 200


def test_t1_1_new_user_dashboard_empty_briefing(test_users):
    """T1.1: Brand-new user without scans receives None instead of leaking previous tenant's briefing."""
    alice, bob, _ = test_users
    store = orchestrator.state_store

    from models import BriefingReport
    alice_briefing = BriefingReport(
        report_id="rep_alice_scan_isolation",
        user_id=alice["id"],
        slot="general",
        executive_summary="Alice's confidential multi-agent portfolio analysis.",
        total_holdings_monitored=5,
        raw_news_count=10
    )
    store.save_briefing(alice_briefing, user_id=alice["id"])
    store.record_user_scan(alice["id"], alice_briefing.report_id, alice_briefing.model_dump(mode="json"))

    # Alice's latest scan is her own briefing
    alice_latest = store.get_latest_user_scan(alice["id"])
    assert alice_latest is not None
    assert alice_latest.report_id == "rep_alice_scan_isolation"

    # Bob's latest scan is strictly None (zero leakage!)
    bob_latest = store.get_latest_user_scan(bob["id"])
    assert bob_latest is None


def test_t1_3_allowlisted_handle_grants_no_operator_key():
    """T1.3: User registering with handle matching allowed telegram name cannot hijack master API key."""
    store = orchestrator.state_store
    # Create or retrieve user named 'forello0' with standard 'user' role
    try:
        user = store.create_user("forello0_untrusted", "untrusted@attacker.com", "pw123", role="user", telegram_username="forello0")
    except Exception:
        user = store.get_user_by_username("forello0_untrusted")

    assert user["role"] == "user"
    resolved_key = orchestrator.resolve_user_api_key(user["id"])
    assert resolved_key == "", "Non-admin user MUST NOT receive system master API key!"


def test_t1_4_duplicate_scan_returns_429(test_users):
    """T1.4: Concurrent scans for the same tenant trigger HTTP 429."""
    from web.app import _ACTIVE_SCANS, _ACTIVE_SCANS_LOCK
    alice, _, _ = test_users
    client = TestClient(app)
    alice_token = create_session_token(alice["id"], alice["username"], role="user")
    client.cookies.set("sentinel_token", alice_token)

    with _ACTIVE_SCANS_LOCK:
        _ACTIVE_SCANS.add(alice["id"])

    try:
        res = client.post("/api/scan")
        assert res.status_code == 429
        assert "already in progress" in res.json()["detail"].lower()
    finally:
        with _ACTIVE_SCANS_LOCK:
            _ACTIVE_SCANS.discard(alice["id"])


def test_t1_2_restore_failure_aborts_backup():
    """T1.2: A failed GCS restore marks state as failed and halts any destructive backup overwrite."""
    import storage.state_store as ss
    orig_state = ss._GCS_RESTORE_STATE
    try:
        ss._GCS_RESTORE_STATE = "failed"
        store = orchestrator.state_store
        result = store.backup_to_gcs(blocking=True)
        assert result is False, "Backup to GCS must abort when restore failed!"
    finally:
        ss._GCS_RESTORE_STATE = orig_state


def test_t1_5_non_admin_cannot_dispatch_to_operator_chat(test_users):
    """T1.5: Non-admin without personal telegram link cannot dispatch briefings to operator Telegram."""
    alice, _, _ = test_users
    store = orchestrator.state_store
    client = TestClient(app)

    # Ensure Alice has no telegram chat ID configured
    with store._get_connection() as conn:
        conn.execute("UPDATE users SET telegram_chat_id = NULL WHERE id = ?", (alice["id"],))
        conn.commit()

    rep_id = "rep_alice_test_dispatch"
    store.record_market_briefing(
        briefing_id=rep_id,
        slot="general",
        message="Test message",
        user_id=alice["id"]
    )

    alice_token = create_session_token(alice["id"], alice["username"], role="user")
    client.cookies.set("sentinel_token", alice_token)

    res = client.post(f"/api/briefings/{rep_id}/dispatch")
    assert res.status_code == 400
    assert "telegram chat id is not configured" in res.json()["detail"].lower()
