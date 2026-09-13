"""
Unit tests for centralized CacheManager and web cache endpoints.
"""
import time
import pytest
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
    from config import config
    correct_pwd = config.dashboard_password or "sentinel_admin"
    auth_headers = {"X-Sentinel-Auth": correct_pwd}
    
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
