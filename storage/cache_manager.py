"""
Centralized Thread-Safe In-Memory Cache Manager for Financial Sentinel.
Provides namespaced TTL caching, hit/miss metrics, singleflight stampede protection,
per-namespace locking, and bounded size eviction.
"""
import time
import threading
from typing import Any, Optional, Dict, Tuple, Callable


class CacheManager:
    """
    Thread-safe in-memory cache supporting:
    - Partitioned per-namespace locking
    - Singleflight stampede protection (get_or_compute via threading.Event)
    - Automatic TTL and capacity-bounded eviction
    - Operational telemetry (hits, misses, ratios)
    """
    def __init__(self, default_ttl_seconds: float = 300.0, max_entries_per_namespace: int = 5000):
        self._default_ttl = default_ttl_seconds
        self._max_entries = max_entries_per_namespace
        self._meta_lock = threading.RLock()
        self._lock = self._meta_lock  # Backward compatibility for direct lock reference
        self._ns_locks: Dict[str, threading.RLock] = {}
        # Structure: { namespace: { key: (expiry_timestamp, value) } }
        self._store: Dict[str, Dict[str, Tuple[float, Any]]] = {}
        # Singleflight coordination: { (namespace, key): threading.Event }
        self._inflight: Dict[Tuple[str, str], threading.Event] = {}
        self._hits: int = 0
        self._misses: int = 0

    def _get_ns_lock(self, namespace: str) -> threading.RLock:
        """Returns dedicated RLock for the specific namespace."""
        with self._meta_lock:
            if namespace not in self._ns_locks:
                self._ns_locks[namespace] = threading.RLock()
            return self._ns_locks[namespace]

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Retrieve value if unexpired; returns default if missing or expired."""
        now = time.time()
        with self._get_ns_lock(namespace):
            ns_dict = self._store.get(namespace)
            if not ns_dict or key not in ns_dict:
                with self._meta_lock:
                    self._misses += 1
                return default

            expiry, val = ns_dict[key]
            if now >= expiry:
                del ns_dict[key]
                with self._meta_lock:
                    self._misses += 1
                return default

            with self._meta_lock:
                self._hits += 1
            return val

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        """Store value under namespace:key with given or default TTL and bounded eviction."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        now = time.time()
        expiry = now + ttl

        with self._get_ns_lock(namespace):
            if namespace not in self._store:
                self._store[namespace] = {}
            ns_dict = self._store[namespace]

            # Bounded capacity eviction if namespace limit is reached
            if len(ns_dict) >= self._max_entries and key not in ns_dict:
                # 1. Evict any already-expired keys
                expired_keys = [k for k, (exp, _) in ns_dict.items() if now >= exp]
                for exp_k in expired_keys:
                    del ns_dict[exp_k]

                # 2. If still at capacity, evict earliest-expiring key (LRU/TTL hybrid)
                if len(ns_dict) >= self._max_entries:
                    earliest_key = min(ns_dict.keys(), key=lambda k: ns_dict[k][0])
                    del ns_dict[earliest_key]

            ns_dict[key] = (expiry, value)

    def get_or_compute(
        self,
        namespace: str,
        key: str,
        compute_fn: Callable[[], Any],
        ttl_seconds: Optional[float] = None,
        timeout: float = 30.0
    ) -> Any:
        """
        Singleflight cache retrieval: prevents cache stampedes by coordinating
        concurrent requests for the same expired/missing key using threading.Event.
        Exactly one thread computes the value; concurrent threads wait on the event.
        """
        # Fast path: check unexpired cache first
        val = self.get(namespace, key)
        if val is not None:
            return val

        coord_key = (namespace, key)
        leader = False

        with self._meta_lock:
            # Re-check under meta lock
            val = self.get(namespace, key)
            if val is not None:
                return val

            event = self._inflight.get(coord_key)
            if event is None:
                event = threading.Event()
                self._inflight[coord_key] = event
                leader = True

        if not leader:
            # Follower: wait for the leader thread to compute
            event.wait(timeout=timeout)
            cached_val = self.get(namespace, key)
            if cached_val is not None:
                return cached_val
            # If still None (e.g. timeout or compute failure), compute independently
            return compute_fn()

        # Leader path: execute computation and populate cache
        try:
            computed_val = compute_fn()
            if computed_val is not None:
                self.set(namespace, key, computed_val, ttl_seconds=ttl_seconds)
            return computed_val
        finally:
            with self._meta_lock:
                self._inflight.pop(coord_key, None)
            event.set()

    def delete(self, namespace: str, key: str) -> bool:
        """Delete specific key from namespace. Returns True if removed."""
        with self._get_ns_lock(namespace):
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
        if namespace:
            with self._get_ns_lock(namespace):
                if namespace in self._store:
                    count = len(self._store[namespace])
                    self._store[namespace].clear()
        else:
            with self._meta_lock:
                for ns, ns_dict in self._store.items():
                    with self._get_ns_lock(ns):
                        count += len(ns_dict)
                        ns_dict.clear()
                self._store.clear()

        return count

    def stats(self) -> Dict[str, Any]:
        """Returns cache telemetry: hits, misses, key count per namespace."""
        now = time.time()
        with self._meta_lock:
            ns_counts = {}
            for ns, keys in self._store.items():
                active = sum(1 for exp, _ in keys.values() if exp > now)
                ns_counts[ns] = active
            total_active = sum(ns_counts.values())
            total_requests = self._hits + self._misses
            ratio = (self._hits / total_requests) if total_requests > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio": round(ratio, 4),
                "total_active_keys": total_active,
                "namespaces": ns_counts,
            }


# Singleton cache manager instance
cache_manager = CacheManager()
