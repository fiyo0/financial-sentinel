"""
Unit tests for centralized CacheManager and web cache endpoints.
"""
import time
from storage.cache_manager import CacheManager, cache_manager
from fastapi.testclient import TestClient
from web.app import app


def test_cache_manager_basic_set_get():
    cm = CacheManager(default_ttl_seconds=2.0)
    cm.set("test_ns", "key1", {"data": 123})

    val = cm.get("test_ns", "key1")
    assert val == {"data": 123}

    # Missing key
    assert cm.get("test_ns", "non_existent") is None
    assert cm.get("test_ns", "non_existent", default=42) == 42


def test_cache_manager_ttl_expiry():
    cm = CacheManager(default_ttl_seconds=0.2)
    cm.set("quotes", "AAPL", 150.0)

    # Immediately available
    assert cm.get("quotes", "AAPL") == 150.0

    # Wait for expiry
    time.sleep(0.3)
    assert cm.get("quotes", "AAPL") is None


def test_cache_manager_namespaces_and_stats():
    cm = CacheManager(default_ttl_seconds=5.0)
    cm.set("quotes", "NVDA", 120.0)
    cm.set("bars", "NVDA", [1, 2, 3])

    assert cm.get("quotes", "NVDA") == 120.0
    assert cm.get("bars", "NVDA") == [1, 2, 3]

    stats = cm.stats()
    assert stats["hits"] >= 2
    assert "quotes" in stats["namespaces"]
    assert "bars" in stats["namespaces"]


def test_cache_manager_clear():
    cm = CacheManager(default_ttl_seconds=5.0)
    cm.set("ns1", "k1", 1)
    cm.set("ns2", "k2", 2)

    # Clear specific namespace
    evicted = cm.clear("ns1")
    assert evicted == 1
    assert cm.get("ns1", "k1") is None
    assert cm.get("ns2", "k2") == 2

    # Clear all
    evicted_all = cm.clear()
    assert evicted_all == 1
    assert cm.get("ns2", "k2") is None


def test_web_cache_endpoints():
    client = TestClient(app)
    from web.app import orchestrator, create_session_token
    admin = orchestrator.state_store.get_or_create_default_admin()
    token = create_session_token(admin["id"], admin["username"], role="admin")
    auth_headers = {"Authorization": f"Bearer {token}"}

    # Pre-populate some cache
    from analytics.market_data import PRICE_CACHE
    from analytics.technical_indicators import BARS_CACHE
    PRICE_CACHE["TEST_SYM"] = (time.time(), {"price": 100})
    BARS_CACHE[("TEST_SYM", 250)] = (time.time(), [100, 101])
    cache_manager.set("test_web", "item", "val")

    # Stats
    res = client.get("/api/cache/stats", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["price_cache_entries"] >= 1
    assert data["bars_cache_entries"] >= 1

    # Clear
    res_clear = client.post("/api/cache/clear", headers=auth_headers)
    assert res_clear.status_code == 200
    clear_data = res_clear.json()
    assert clear_data["status"] == "success"
    assert clear_data["cleared"]["price_cache_entries"] >= 1
    assert clear_data["cleared"]["bars_cache_entries"] >= 1


def test_cache_manager_singleflight_stampede():
    """Verify singleflight coordinates concurrent threads: compute function runs exactly once."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    cm = CacheManager(default_ttl_seconds=10.0)
    compute_count = 0
    lock = threading.Lock()

    def expensive_computation():
        nonlocal compute_count
        with lock:
            compute_count += 1
        time.sleep(0.05)  # Simulate network latency
        return {"data": 999}

    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        return cm.get_or_compute("sf_ns", "shared_key", expensive_computation)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker) for _ in range(10)]
        results = [f.result() for f in futures]

    assert len(results) == 10
    for r in results:
        assert r == {"data": 999}
    assert compute_count == 1, f"Expected compute_fn to execute once, but ran {compute_count} times"


def test_cache_manager_capacity_bounded_eviction():
    """Verify namespace honors max_entries_per_namespace and evicts earliest expiring keys."""
    cm = CacheManager(default_ttl_seconds=100.0, max_entries_per_namespace=5)
    for i in range(10):
        cm.set("bounded_ns", f"key_{i}", f"val_{i}")

    stats = cm.stats()
    assert stats["namespaces"]["bounded_ns"] <= 5
    # The latest items should be preserved
    assert cm.get("bounded_ns", "key_9") == "val_9"
    assert cm.get("bounded_ns", "key_8") == "val_8"


def test_price_cache_bounded_proxy():
    """Verify BoundedPriceCache maintains dict interface and bounds."""
    from analytics.market_data import PRICE_CACHE
    PRICE_CACHE.clear()

    PRICE_CACHE["NVDA"] = (time.time(), {"price": 130.0, "name": "NVIDIA"})
    assert "NVDA" in PRICE_CACHE
    assert PRICE_CACHE["NVDA"][1]["price"] == 130.0
    assert len(PRICE_CACHE) == 1

    PRICE_CACHE.clear()
    assert len(PRICE_CACHE) == 0
    assert "NVDA" not in PRICE_CACHE

