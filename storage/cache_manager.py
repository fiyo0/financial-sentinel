"""
Centralized Thread-Safe In-Memory Cache Manager for Financial Sentinel.
Provides namespaced TTL caching, hit/miss metrics, and instant invalidation.
"""
import time
import threading
from typing import Any, Optional, Dict, Tuple


class CacheManager:
    """
    Thread-safe in-memory cache supporting namespaces, individual TTLs, and metrics.
    """
    def __init__(self, default_ttl_seconds: float = 300.0):
        self._default_ttl = default_ttl_seconds
        self._lock = threading.RLock()
        # Structure: { namespace: { key: (expiry_timestamp, value) } }
        self._store: Dict[str, Dict[str, Tuple[float, Any]]] = {}
        self._hits: int = 0
        self._misses: int = 0

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Retrieve value if unexpired; returns default if missing or expired."""
        now = time.time()
        with self._lock:
            ns_dict = self._store.get(namespace)
            if not ns_dict or key not in ns_dict:
                self._misses += 1
                return default

            expiry, val = ns_dict[key]
            if now >= expiry:
                del ns_dict[key]
                self._misses += 1
                return default

            self._hits += 1
            return val

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        """Store value under namespace:key with given or default TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expiry = time.time() + ttl
        with self._lock:
            if namespace not in self._store:
                self._store[namespace] = {}
            self._store[namespace][key] = (expiry, value)

    def delete(self, namespace: str, key: str) -> bool:
        """Delete specific key from namespace. Returns True if removed."""
        with self._lock:
            ns_dict = self._store.get(namespace)
            if ns_dict and key in ns_dict:
                del ns_dict[key]
                return True
            return False

    def clear(self, namespace: Optional[str] = None) -> int:
        """
        Clears all keys in a namespace, or all namespaces if None.
        Returns the number of keys evicted.
        """
        count = 0
        with self._lock:
            if namespace:
                if namespace in self._store:
                    count = len(self._store[namespace])
                    self._store[namespace].clear()
            else:
                for _ns, keys in self._store.items():
                    count += len(keys)
                self._store.clear()

        return count

    def stats(self) -> Dict[str, Any]:
        """Returns cache telemetry: hits, misses, key count per namespace."""
        now = time.time()
        with self._lock:
            ns_counts = {}
            for ns, keys in self._store.items():
                active = sum(1 for exp, _ in keys.values() if exp > now)
                ns_counts[ns] = active
            total_active = sum(ns_counts.values())
            ratio = (self._hits / (self._hits + self._misses)) if (self._hits + self._misses) > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio": round(ratio, 4),
                "total_active_keys": total_active,
                "namespaces": ns_counts,
            }


# Singleton cache manager instance
cache_manager = CacheManager()
