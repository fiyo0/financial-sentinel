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
