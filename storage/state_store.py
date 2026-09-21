"""
Persistent storage and deduplication state management using SQLite.
"""
import sqlite3
import json
import hashlib
import os
import logging
import contextlib
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from models import NewsItem, BriefingReport, NewsCategory

import threading

logger = logging.getLogger(__name__)

# Process-level guard to ensure GCS restore runs at most ONCE per container lifecycle
_GCS_RESTORED = False
_GCS_RESTORE_LOCK = threading.Lock()
_GCS_RESTORE_STATE: str = "unknown"  # "unknown", "restored", "no_remote_blob", "failed"
_GCS_REMOTE_GENERATION: Optional[int] = None

# Serialized, debounced GCS backup worker state
_GCS_BACKUP_LOCK = threading.Lock()
_GCS_BACKUP_WORKER_LOCK = threading.Lock()
_GCS_BACKUP_PENDING = False
_GCS_BACKUP_THREAD: Optional[threading.Thread] = None

# Cached externalized sector taxonomy & reference equities
_SECTOR_TAXONOMY_CACHE: Optional[Dict[str, Any]] = None
_REFERENCE_EQUITIES_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def get_reference_equities() -> Dict[str, Dict[str, Any]]:
    """
    Retrieves the canonical reference equities mapped by ticker symbol (e.g., 'AAPL': {...}).
    Cached in-memory as a singleton to eliminate redundant disk I/O across agents.
    """
    global _REFERENCE_EQUITIES_CACHE
    if _REFERENCE_EQUITIES_CACHE is not None:
        return _REFERENCE_EQUITIES_CACHE

    res: Dict[str, Dict[str, Any]] = {}
    try:
        ref_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_equities.json")
        if os.path.exists(ref_path):
            with open(ref_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            for e in entries:
                sym = e.get("ticker", "").strip().upper()
                if sym:
                    res[sym] = e
            _REFERENCE_EQUITIES_CACHE = res
            return _REFERENCE_EQUITIES_CACHE
    except Exception as e:
        logger.debug(f"Failed to load reference_equities.json: {e}")

    _REFERENCE_EQUITIES_CACHE = res
    return _REFERENCE_EQUITIES_CACHE


def get_sector_taxonomy() -> Dict[str, Any]:
    """
    Retrieves the canonical 11-GICS sector taxonomy and macro topical keywords.
    Cached in-memory for O(1) evaluation speed across agents.
    """
    global _SECTOR_TAXONOMY_CACHE
    if _SECTOR_TAXONOMY_CACHE is not None:
        return _SECTOR_TAXONOMY_CACHE

    try:
        tax_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sector_taxonomy.json")
        if os.path.exists(tax_path):
            with open(tax_path, "r", encoding="utf-8") as f:
                _SECTOR_TAXONOMY_CACHE = json.load(f)
                return _SECTOR_TAXONOMY_CACHE
    except Exception as e:
        logger.debug(f"Failed to load sector_taxonomy.json: {e}")

    return {}


_SOURCES_REGISTRY_CACHE: Optional[Dict[str, Any]] = None


def get_sources_registry() -> Dict[str, Any]:
    """
    Retrieves externalized source reliability scores and pattern categories.
    Cached in-memory as a singleton to eliminate redundant disk I/O.
    """
    global _SOURCES_REGISTRY_CACHE
    if _SOURCES_REGISTRY_CACHE is not None:
        return _SOURCES_REGISTRY_CACHE

    try:
        reg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources_registry.json")
        if os.path.exists(reg_path):
            with open(reg_path, "r", encoding="utf-8") as f:
                _SOURCES_REGISTRY_CACHE = json.load(f)
                return _SOURCES_REGISTRY_CACHE
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load sources_registry.json: {e}")

    return {}


class StateStore:

    def __init__(self, db_path: str = "storage/state.db"):
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        if not _GCS_RESTORED:
            self.restore_from_gcs()
        self._init_db()

    def restore_from_gcs(self, force: bool = False) -> bool:
        """Restores state.db from Google Cloud Storage on Cloud Run startup with timestamp safety check."""
        global _GCS_RESTORED, _GCS_RESTORE_STATE, _GCS_REMOTE_GENERATION
        if _GCS_RESTORED and not force:
            return True

        is_cloud_run = bool(os.getenv("K_SERVICE") or os.getenv("GCS_SYNC_ENABLED") == "true")
        if not is_cloud_run:
            _GCS_RESTORE_STATE = "not_cloud_run"
            return False

        with _GCS_RESTORE_LOCK:
            if _GCS_RESTORED and not force:
                return True

            bucket_name = os.getenv("GCS_DATA_BUCKET", "financial-sentinel-data-507007")
            if not bucket_name:
                _GCS_RESTORE_STATE = "no_bucket"
                return False
            try:
                from google.cloud import storage
                client = storage.Client()
                bucket = client.bucket(bucket_name)
                blob = bucket.blob("state.db")
                if blob.exists():
                    blob.reload()
                    _GCS_REMOTE_GENERATION = blob.generation
                    # Timestamp check: avoid overwriting newer local state with older GCS snapshot
                    if os.path.exists(self.db_path) and not force:
                        try:
                            gcs_updated = blob.updated
                            if gcs_updated:
                                local_mtime = os.path.getmtime(self.db_path)
                                if local_mtime >= gcs_updated.timestamp():
                                    logger.info(
                                        "Local state.db is newer than or equal to GCS snapshot (local: %s, gcs: %s). Skipping restore.",
                                        local_mtime, gcs_updated.timestamp()
                                    )
                                    _GCS_RESTORED = True
                                    _GCS_RESTORE_STATE = "restored"
                                    return True
                        except Exception as time_err:
                            logger.debug("Could not verify GCS blob timestamp: %s", time_err)

                    self.close_connection()
                    os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
                    # Purge stale WAL/SHM files to prevent corruption or replay
                    for suffix in ("-wal", "-shm"):
                        stale_file = f"{self.db_path}{suffix}"
                        if os.path.exists(stale_file):
                            try:
                                os.remove(stale_file)
                            except OSError as e:
                                logger.debug("Could not remove stale WAL file %s: %s", stale_file, e)
                    blob.download_to_filename(self.db_path)
                    logger.info("Successfully restored state.db from GCS. Running integrity check & migrations...")
                    try:
                        with sqlite3.connect(self.db_path) as check_conn:
                            res = check_conn.execute("PRAGMA integrity_check;").fetchone()
                            if not res or res[0] != "ok":
                                logger.critical(f"Corrupted state.db snapshot restored from GCS: {res}")
                                _GCS_RESTORE_STATE = "failed"
                                raise RuntimeError(f"Corrupted state.db snapshot: {res}")
                    except Exception as err:
                        _GCS_RESTORE_STATE = "failed"
                        logger.error(f"State store integrity check failed on restore: {err}")
                        raise
                    try:
                        with self._get_connection() as conn:
                            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    except sqlite3.Error as e:
                        logger.warning("Post-restore WAL checkpoint warning: %s", e)
                    _GCS_RESTORED = True
                    _GCS_RESTORE_STATE = "restored"
                    self._init_db()
                    return True
                else:
                    logger.info("No remote state.db found in GCS bucket %s. Starting fresh.", bucket_name)
                    _GCS_RESTORED = True
                    _GCS_RESTORE_STATE = "no_remote_blob"
                    _GCS_REMOTE_GENERATION = None
                    return False
            except Exception as e:
                _GCS_RESTORE_STATE = "failed"
                logger.critical(f"Failed to restore state.db from GCS: {e}")
                return False
        return False

    def backup_to_gcs(self, blocking: bool = False) -> bool:
        """Backs up state.db to Google Cloud Storage on Cloud Run mutations with serialized debouncing.
        
        Args:
            blocking: If True, executes upload synchronously inline.
                      If False, queues a debounced background upload ensuring exactly one upload runs at a time.
        """
        is_cloud_run = bool(os.getenv("K_SERVICE") or os.getenv("GCS_SYNC_ENABLED") == "true")
        if not is_cloud_run:
            return False
        if _GCS_RESTORE_STATE == "failed":
            logger.critical("Aborting GCS backup: startup restore failed; refusing to overwrite remote state with potentially empty/corrupted local DB.")
            return False
        bucket_name = os.getenv("GCS_DATA_BUCKET", "financial-sentinel-data-507007")
        if not bucket_name or not os.path.exists(self.db_path):
            return False

        def _do_upload_cycle():
            global _GCS_BACKUP_PENDING, _GCS_REMOTE_GENERATION
            with _GCS_BACKUP_LOCK:
                while True:
                    _GCS_BACKUP_PENDING = False
                    import tempfile
                    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
                    os.close(tmp_fd)
                    try:
                        # 1. Take a consistent, atomic snapshot using SQLite Online Backup API
                        src = sqlite3.connect(self.db_path)
                        dst = sqlite3.connect(tmp_path)
                        with dst:
                            src.backup(dst)
                        dst.close()
                        src.close()

                        # 2. Upload the consistent point-in-time snapshot to GCS
                        from google.cloud import storage
                        client = storage.Client()
                        bucket = client.bucket(bucket_name)
                        blob = bucket.blob("state.db")
                        upload_kwargs = {}
                        if _GCS_REMOTE_GENERATION is not None:
                            upload_kwargs["if_generation_match"] = _GCS_REMOTE_GENERATION
                        else:
                            upload_kwargs["if_generation_match"] = 0
                        blob.upload_from_filename(tmp_path, **upload_kwargs)
                        if blob.generation:
                            _GCS_REMOTE_GENERATION = blob.generation
                        logger.info("Successfully backed up consistent state.db snapshot to GCS (generation %s).", _GCS_REMOTE_GENERATION)
                    except Exception as e:
                        logger.warning(f"Failed to snapshot and upload state.db to GCS: {e}")
                    finally:
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError as e:
                                logger.debug("Failed to remove tmp snapshot %s: %s", tmp_path, e)

                    # If another mutation arrived while this upload was in flight, coalesce and run once more
                    if not _GCS_BACKUP_PENDING:
                        break

        if blocking:
            _do_upload_cycle()
            return True
        else:
            global _GCS_BACKUP_PENDING, _GCS_BACKUP_THREAD
            _GCS_BACKUP_PENDING = True
            with _GCS_BACKUP_WORKER_LOCK:
                if _GCS_BACKUP_THREAD is None or not _GCS_BACKUP_THREAD.is_alive():
                    _GCS_BACKUP_THREAD = threading.Thread(target=_do_upload_cycle, daemon=True)
                    _GCS_BACKUP_THREAD.start()
            return True


    def _get_connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1;")
                return conn
            except sqlite3.Error as e:
                logger.debug("Cached DB connection failed healthcheck: %s", e)
                try:
                    conn.close()
                except sqlite3.Error as close_err:
                    logger.error("Failed closing stale DB connection: %s", close_err)
                self._local.conn = None

        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=30000;")
        except sqlite3.Error as e:
            logger.debug("PRAGMA configuration note on connection: %s", e)
        self._local.conn = conn
        return conn

    @contextlib.contextmanager
    def _write_transaction(self):
        """
        Executes writes inside an explicit BEGIN IMMEDIATE transaction.
        Acquires write lock immediately, eliminating reader-to-writer lock upgrade deadlocks.
        """
        conn = self._get_connection()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except (sqlite3.Error, Exception) as e:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                pass
            logger.error("Transaction rolled back due to error: %s", e)
            raise

    def wal_checkpoint(self, mode: str = "TRUNCATE") -> bool:
        """Executes a synchronous SQLite WAL checkpoint to flush WAL pages to the main database file."""
        try:
            with self._get_connection() as conn:
                conn.execute(f"PRAGMA wal_checkpoint({mode});")
            logger.info("Executed SQLite PRAGMA wal_checkpoint(%s).", mode)
            return True
        except sqlite3.Error as e:
            logger.warning("SQLite WAL checkpoint (%s) failed: %s", mode, e)
            return False
        except Exception as e:
            logger.warning("Unexpected error during SQLite WAL checkpoint (%s): %s", mode, e)
            return False

    def close_connection(self):
        """Closes the current thread's connection pool entry if open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error as e:
                logger.warning("Failed closing DB connection: %s", e)
            self._local.conn = None


    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Ingested news table (for deduplication & story evolution)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingested_news (
                    id TEXT PRIMARY KEY,
                    raw_hash TEXT UNIQUE,
                    title TEXT NOT NULL,
                    source TEXT,
                    url TEXT,
                    published_at TIMESTAMP,
                    category TEXT,
                    reliability_score REAL,
                    related_tickers TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Processed alerts & analyses
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_alerts (
                    id TEXT PRIMARY KEY,
                    alert_type TEXT,
                    ticker TEXT,
                    priority TEXT,
                    impact TEXT,
                    news_item_id TEXT,
                    payload_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(news_item_id) REFERENCES ingested_news(id)
                )
            """)

            # Generated briefings history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS briefing_history (
                    report_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    slot TEXT,
                    generated_at TIMESTAMP,
                    executive_summary TEXT,
                    raw_news_count INTEGER,
                    payload_json TEXT,
                    dispatched_channels TEXT
                )
            """)
            try:
                cursor.execute("PRAGMA table_info(briefing_history);")
                cols = [c[1] for c in cursor.fetchall()]
                migrated = False
                if "user_id" not in cols:
                    cursor.execute("ALTER TABLE briefing_history ADD COLUMN user_id TEXT;")
                    migrated = True
                if "slot" not in cols:
                    cursor.execute("ALTER TABLE briefing_history ADD COLUMN slot TEXT;")
                    migrated = True
                if migrated:
                    conn.commit()
                    logger.info("Successfully migrated briefing_history schema: user_id and slot columns added.")
            except Exception as e:
                logger.error(f"Error checking or migrating briefing_history schema: {e}")

            # User feedback for tuning the Critic Agent
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    feedback_type TEXT NOT NULL, -- 'accurate', 'false_positive', 'noise', 'missed_context'
                    user_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Key-value state / active portfolio storage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Dynamic Ticker Brand & Alias Registry
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ticker_aliases (
                    ticker TEXT PRIMARY KEY,
                    company_name TEXT,
                    aliases_json TEXT,
                    sector TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                cursor.execute("PRAGMA table_info(ticker_aliases);")
                alias_cols = [c[1] for c in cursor.fetchall()]
                if "sector" not in alias_cols:
                    cursor.execute("ALTER TABLE ticker_aliases ADD COLUMN sector TEXT;")
                    conn.commit()
            except Exception as e:
                logger.debug(f"Error verifying ticker_aliases sector column: {e}")


            # Multi-User Identity & Security
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    telegram_username TEXT,
                    telegram_chat_id TEXT,
                    encrypted_gemini_key TEXT,
                    role TEXT DEFAULT 'user',
                    token_epoch INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Multi-User Isolated Portfolios
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_portfolios (
                    user_id TEXT PRIMARY KEY,
                    portfolio_name TEXT,
                    cash REAL DEFAULT 10000.0,
                    holdings_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Multi-User Isolated Scans & History
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    report_id TEXT,
                    payload_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Multi-User Isolated Deep Dives Repository
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_deepdives (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    company_name TEXT,
                    current_price REAL,
                    verdict TEXT,
                    conviction_score REAL,
                    technicals_json TEXT,
                    sentiment_json TEXT,
                    analysis_text TEXT,
                    payload_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Token usage & cost tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT,
                    prompt_tokens INTEGER,
                    cached_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    cost_usd REAL,
                    model_name TEXT,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Telegram processed updates deduplication table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id INTEGER PRIMARY KEY,
                    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Ground truth thesis outcomes ledger for critic calibration (§4A)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thesis_outcomes (
                    thesis_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    stance TEXT NOT NULL,
                    conviction_pct REAL NOT NULL,
                    critic_verdict TEXT,
                    critic_conf_pct REAL,
                    entry_price REAL NOT NULL,
                    entry_date TEXT NOT NULL,
                    return_5d REAL,
                    return_21d REAL,
                    return_63d REAL,
                    benchmark_21d REAL,
                    resolved_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Deterministic schema migrations without silent exception suppression
            cursor.execute("PRAGMA table_info(users);")
            user_cols = {row[1] for row in cursor.fetchall()}
            if "token_epoch" not in user_cols:
                cursor.execute("ALTER TABLE users ADD COLUMN token_epoch INTEGER DEFAULT 1;")

            cursor.execute("PRAGMA table_info(token_usage);")
            token_cols = {row[1] for row in cursor.fetchall()}
            if "user_id" not in token_cols:
                cursor.execute("ALTER TABLE token_usage ADD COLUMN user_id TEXT;")
            if "cached_tokens" not in token_cols:
                cursor.execute("ALTER TABLE token_usage ADD COLUMN cached_tokens INTEGER DEFAULT 0;")

            # High-performance indexes for historical scalability
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_briefings_user_slot ON briefing_history(user_id, slot, generated_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_scans_user ON user_scans(user_id, created_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_deepdives_user ON user_deepdives(user_id, created_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_deepdives_ticker ON user_deepdives(user_id, ticker);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage(user_id, created_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ingested_news_created ON ingested_news(created_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_thesis_outcomes_user ON thesis_outcomes(user_id, created_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_thesis_outcomes_ticker ON thesis_outcomes(ticker);")

            conn.commit()

        # Self-seed canonical equity directory if ticker_aliases is empty
        self._seed_canonical_equities_if_needed()

        # Run one-time forced credential rotation migration on boot (R-1)
        self.rotate_legacy_admin_credentials()

    def _seed_canonical_equities_if_needed(self):
        """
        Hermetically self-seeds canonical equity aliases and sectors from reference dataset
        into ticker_aliases if the registry is uninitialized or missing sectors.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM ticker_aliases")
                count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM ticker_aliases WHERE sector IS NOT NULL")
                with_sector = cursor.fetchone()[0]
                if count > 0 and with_sector >= count:
                    return

                ref_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_equities.json")
                if not os.path.exists(ref_path):
                    return

                with open(ref_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)

                rows = []
                for entry in entries:
                    sym = entry["ticker"].strip().upper()
                    name = entry.get("name", sym)
                    aliases = entry.get("aliases", [sym])
                    sec = entry.get("sector", "Unclassified")
                    rows.append((sym, name, json.dumps(aliases), sec))

                cursor.executemany("""
                    INSERT INTO ticker_aliases (ticker, company_name, aliases_json, sector, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(ticker) DO UPDATE SET
                        company_name = excluded.company_name,
                        aliases_json = excluded.aliases_json,
                        sector = excluded.sector,
                        updated_at = CURRENT_TIMESTAMP
                """, rows)
                conn.commit()
                logger.info(f"Self-seeded {len(rows)} canonical equities with sectors into ticker_aliases registry.")
        except Exception as e:
            logger.debug(f"Canonical equity auto-seeding skipped or failed: {e}")





    @staticmethod
    def compute_hash(title: str, source: str) -> str:
        normalized = f"{title.strip().lower()}|{source.strip().lower()}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def is_news_processed(self, raw_hash: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM ingested_news WHERE raw_hash = ?", (raw_hash,))
            return cursor.fetchone() is not None

    def save_news_item(self, item: NewsItem) -> bool:
        if not item.raw_hash:
            item.raw_hash = self.compute_hash(item.title, item.source)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO ingested_news (
                        id, raw_hash, title, source, url, published_at, category, reliability_score, related_tickers
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.id,
                    item.raw_hash,
                    item.title,
                    item.source,
                    item.url,
                    item.published_at.isoformat(),
                    item.category.value,
                    item.source_reliability_score,
                    json.dumps(item.related_tickers)
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_recent_news(self, hours: int = 24, limit: int = 50) -> List[NewsItem]:
        """
        Retrieves recently ingested news items within the specified lookback window,
        sorted chronologically by published_at DESC.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        items: List[NewsItem] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, raw_hash, title, source, url, published_at, category, reliability_score, related_tickers
                FROM ingested_news
                WHERE published_at >= ?
                ORDER BY published_at DESC
                LIMIT ?
            """, (cutoff, limit))
            for row in cursor.fetchall():
                try:
                    raw_val = str(row[5]).strip() if row[5] is not None else ""
                    if raw_val.endswith("Z"):
                        raw_val = raw_val[:-1] + "+00:00"
                    pub_dt = datetime.fromisoformat(raw_val)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pub_dt = datetime.now(timezone.utc)
                try:
                    cat = NewsCategory(row[6])
                except (ValueError, KeyError):
                    cat = NewsCategory.BREAKING
                try:
                    tickers = json.loads(row[8]) if row[8] else []
                except (json.JSONDecodeError, TypeError):
                    tickers = []

                items.append(NewsItem(
                    id=row[0],
                    raw_hash=row[1],
                    title=row[2],
                    source=row[3],
                    url=row[4] or "",
                    published_at=pub_dt,
                    summary=row[2],
                    category=cat,
                    source_reliability_score=row[7] or 0.8,
                    related_tickers=tickers,
                    related_sectors=[]
                ))
        return items

    def get_recent_news_for_ticker(self, ticker: str, aliases: Optional[List[str]] = None, hours: int = 48, limit: int = 30) -> List[NewsItem]:
        """
        Retrieves stored news items for a specific ticker and its brand aliases from SQLite.
        """
        import re
        all_recent = self.get_recent_news(hours=hours, limit=250)
        clean_ticker = ticker.strip().upper()
        alias_tokens = [clean_ticker.lower()]
        if aliases:
            alias_tokens.extend([a.lower() for a in aliases if a.strip()])

        matched: List[NewsItem] = []
        for item in all_recent:
            if clean_ticker in [t.upper() for t in item.related_tickers]:
                matched.append(item)
                continue
            title_lower = (item.title or "").lower()
            if any(re.search(r'\b' + re.escape(tok) + r'\b', title_lower) for tok in alias_tokens):
                matched.append(item)
        return matched[:limit]

    def get_recent_regulatory_news(self, hours: int = 72, limit: int = 25) -> List[NewsItem]:
        """
        Retrieves authoritative regulatory bulletins, SEC filings/orders, and central bank actions
        within the specified lookback window, sorted chronologically.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        items: List[NewsItem] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, raw_hash, title, source, url, published_at, category, reliability_score, related_tickers
                FROM ingested_news
                WHERE published_at >= ?
                  AND (
                    source LIKE '%sec.gov%' OR
                    source LIKE '%federalreserve%' OR
                    source LIKE '%cftc%' OR
                    source LIKE '%SEC Press%' OR
                    source LIKE '%SEC Regulatory%' OR
                    source LIKE '%Federal Reserve%' OR
                    category = 'SEC_FILING' OR
                    title LIKE '%SEC %' OR
                    title LIKE '%Securities and Exchange Commission%' OR
                    title LIKE '%Federal Reserve%' OR
                    title LIKE '%CFTC%' OR
                    title LIKE '%FTC %' OR
                    title LIKE '%DOJ %' OR
                    title LIKE '%Exemptive Order%'
                  )
                ORDER BY published_at DESC
                LIMIT ?
            """, (cutoff, limit))
            for row in cursor.fetchall():
                try:
                    raw_val = str(row[5]).strip() if row[5] is not None else ""
                    if raw_val.endswith("Z"):
                        raw_val = raw_val[:-1] + "+00:00"
                    pub_dt = datetime.fromisoformat(raw_val)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pub_dt = datetime.now(timezone.utc)
                try:
                    cat = NewsCategory(row[6])
                except (ValueError, KeyError):
                    cat = NewsCategory.SEC_FILING
                try:
                    tickers = json.loads(row[8]) if row[8] else []
                except (json.JSONDecodeError, TypeError):
                    tickers = []

                items.append(NewsItem(
                    id=row[0],
                    raw_hash=row[1],
                    title=row[2],
                    source=row[3],
                    url=row[4] or "",
                    published_at=pub_dt,
                    summary=row[2],
                    category=cat,
                    source_reliability_score=row[7] or 0.95,
                    related_tickers=tickers,
                    related_sectors=["Financials"]
                ))
        return items

    def save_ticker_aliases(self, ticker: str, company_name: str, aliases: List[str], sector: Optional[str] = None) -> bool:
        """
        Persists dynamically resolved brand aliases and sector for a ticker.
        """
        clean_ticker = ticker.strip().upper()
        clean_aliases = sorted(list(set(aliases)))
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO ticker_aliases (ticker, company_name, aliases_json, sector, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(ticker) DO UPDATE SET
                        company_name = excluded.company_name,
                        aliases_json = excluded.aliases_json,
                        sector = COALESCE(excluded.sector, ticker_aliases.sector),
                        updated_at = CURRENT_TIMESTAMP
                """, (clean_ticker, company_name, json.dumps(clean_aliases), sector))
                conn.commit()
            return True
        except Exception as e:
            logger.warning(f"Error saving ticker aliases for {clean_ticker}: {e}")
            return False

    def get_ticker_sector(self, ticker: str) -> Optional[str]:
        """
        Retrieves canonical sector for a ticker from SQLite or canonical reference equities.
        """
        clean_ticker = ticker.strip().upper()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT sector FROM ticker_aliases WHERE ticker = ?", (clean_ticker,))
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0]
        except sqlite3.Error as e:
            logger.debug("Failed to query ticker sector for %s: %s", clean_ticker, e)

        # Fallback to in-memory cached reference dataset check
        equities = get_reference_equities()
        if clean_ticker in equities:
            return equities[clean_ticker].get("sector")
        return None


    def get_ticker_aliases(self, ticker: str) -> Optional[List[str]]:
        """
        Retrieves cached brand aliases for a ticker from SQLite.
        """
        clean_ticker = ticker.strip().upper()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT aliases_json FROM ticker_aliases WHERE ticker = ?", (clean_ticker,))
                row = cursor.fetchone()
                if row and row[0]:
                    return json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.debug("Failed to get aliases for %s: %s", clean_ticker, e)
        return None

    def get_all_ticker_aliases(self) -> Dict[str, List[str]]:
        """
        Retrieves all dynamically cached ticker aliases across the system.
        """
        res: Dict[str, List[str]] = {}
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ticker, aliases_json FROM ticker_aliases")
                for row in cursor.fetchall():
                    if row[0] and row[1]:
                        try:
                            res[row[0]] = json.loads(row[1])
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.debug("Malformed alias JSON for %s: %s", row[0], e)
        except sqlite3.Error as e:
            logger.debug("Failed to get all ticker aliases: %s", e)
        return res

    def get_sector_taxonomy(self) -> Dict[str, Any]:
        """
        Retrieves the canonical 11-GICS sector taxonomy and macro topical keywords.
        Cached in-memory for O(1) evaluation speed across agents.
        """
        return get_sector_taxonomy()

    def save_briefing(self, briefing: BriefingReport, user_id: Optional[str] = None):
        target_user = user_id or briefing.user_id
        target_slot = briefing.slot or "general"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO briefing_history (
                    report_id, user_id, slot, generated_at, executive_summary, raw_news_count, payload_json, dispatched_channels
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                briefing.report_id,
                target_user,
                target_slot,
                briefing.generated_at.isoformat(),
                briefing.executive_summary,
                briefing.raw_news_count,
                briefing.model_dump_json(),
                json.dumps(briefing.dispatched_channels)
            ))
            conn.commit()
        # Also cache in kv_store namespaced by user so scans never leak or collide across tenants
        try:
            kv_key = f"latest_scan_briefing:{target_user}" if target_user else "latest_scan_briefing"
            self.set_kv(kv_key, briefing.model_dump(mode="json"))
        except Exception as e:
            logger.warning("Failed to cache latest scan briefing in KV store: %s", e)

    def get_latest_scan_briefing(self) -> Optional[BriefingReport]:
        """Returns the latest scheduled system-wide briefing (e.g. premarket, postmarket, weekend)."""
        cached = self.get_kv("latest_scan_briefing")
        if cached and isinstance(cached, dict) and "report_id" in cached:
            try:
                return BriefingReport(**cached)
            except Exception as e:
                logger.warning("Malformed cached briefing in KV store: %s", e)

        # Fallback: scan recent scheduled system briefing history
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT payload_json FROM briefing_history
                WHERE user_id IS NULL AND slot IS NOT NULL
                ORDER BY generated_at DESC
                LIMIT 15
            """)
            for row in cursor.fetchall():
                if row["payload_json"]:
                    try:
                        d = json.loads(row["payload_json"])
                        if "total_holdings_monitored" in d or "critical_risk_alerts" in d:
                            return BriefingReport(**d)
                    except (json.JSONDecodeError, TypeError, ValueError) as e:
                        logger.debug("Failed parsing historical briefing payload: %s", e)
                        continue
        return None

    def record_briefing(self, briefing_id: str, payload: Dict[str, Any]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            user_id = payload.get("user_id")
            slot = payload.get("slot")
            cursor.execute("""
                INSERT OR REPLACE INTO briefing_history (
                    report_id, user_id, slot, generated_at, executive_summary, raw_news_count, payload_json, dispatched_channels
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                briefing_id,
                user_id,
                slot,
                payload.get("generated_at", datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + "Z"),
                payload.get("message", "")[:300],
                payload.get("total_holdings", 0),
                json.dumps(payload),
                json.dumps(payload.get("dispatched_channels", ["telegram"]))
            ))
            conn.commit()

    def record_market_briefing(
        self,
        briefing_id: str,
        slot: str,
        message: str,
        user_id: Optional[str] = None,
        dispatched_channels: Optional[List[str]] = None,
        extra_payload: Optional[Dict[str, Any]] = None
    ):
        now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + "Z"
        payload = {
            "slot": slot,
            "message": message,
            "user_id": user_id,
            "generated_at": now_iso,
            **(extra_payload or {})
        }
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO briefing_history (
                    report_id, user_id, slot, generated_at, executive_summary, raw_news_count, payload_json, dispatched_channels
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                briefing_id,
                user_id,
                slot,
                now_iso,
                message[:300],
                payload.get("total_holdings", 0),
                json.dumps(payload),
                json.dumps(dispatched_channels or [])
            ))
            conn.commit()
        self.backup_to_gcs(blocking=True)

    def get_market_briefings(
        self,
        user_id: Optional[str] = None,
        slot: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT report_id, user_id, slot, generated_at, executive_summary, raw_news_count, payload_json, dispatched_channels
                FROM briefing_history
            """
            params = []
            conditions = []
            if user_id:
                conditions.append("user_id = ?")
                params.append(user_id)
            if slot:
                conditions.append("slot = ?")
                params.append(slot)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY generated_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            results = []
            seen_fingerprints = set()
            for r in rows:
                p = {}
                if r["payload_json"]:
                    try:
                        p = json.loads(r["payload_json"])
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.debug("Malformed payload_json for briefing %s: %s", r["report_id"], e)
                        p = {}
                dispatched = []
                if r["dispatched_channels"]:
                    try:
                        dispatched = json.loads(r["dispatched_channels"])
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.debug("Malformed dispatched_channels for briefing %s: %s", r["report_id"], e)
                        dispatched = []

                slot_val = r["slot"] or p.get("slot", "general")
                date_prefix = (r["generated_at"] or "")[:13]  # YYYY-MM-DDTHH
                summary_prefix = (r["executive_summary"] or "").strip()[:80]
                fp = (slot_val, date_prefix, summary_prefix)

                # Skip identical duplicate briefings generated in the same slot window
                if fp in seen_fingerprints:
                    continue
                seen_fingerprints.add(fp)

                results.append({
                    "report_id": r["report_id"],
                    "user_id": r["user_id"],
                    "slot": slot_val,
                    "generated_at": r["generated_at"],
                    "executive_summary": r["executive_summary"],
                    "message_html": p.get("message", r["executive_summary"]),
                    "payload": p,
                    "dispatched_channels": dispatched
                })
            return results

    def prune_briefings(self, retention_days: Optional[int] = 30) -> int:
        """Prunes duplicate briefing entries and purges records older than retention_days."""
        total_deleted = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Deduplicate: remove redundant duplicate rows, preserving the oldest canonical entry per user/slot/hour/summary
            cursor.execute("""
                DELETE FROM briefing_history
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM briefing_history
                    GROUP BY user_id, slot, substr(generated_at, 1, 13), substr(executive_summary, 1, 60)
                )
            """)
            total_deleted += cursor.rowcount

            # 2. Purge expired records if retention_days specified
            if retention_days and retention_days > 0:
                cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
                cursor.execute("DELETE FROM briefing_history WHERE generated_at < ?", (cutoff_date,))
                total_deleted += cursor.rowcount

            conn.commit()

        if total_deleted > 0:
            logger.info(f"Pruned {total_deleted} old or duplicate briefing records from briefing_history.")
            self.backup_to_gcs(blocking=True)

        return total_deleted

    def get_market_briefing_by_id(self, report_id: str, user_id: Optional[str] = None, is_admin: bool = False) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT report_id, user_id, slot, generated_at, executive_summary, raw_news_count, payload_json, dispatched_channels
                FROM briefing_history
                WHERE report_id = ?
            """, (report_id,))
            r = cursor.fetchone()
            if not r:
                return None
            if not is_admin and user_id and r["user_id"] != user_id:
                return None
            p = {}
            if r["payload_json"]:
                try:
                    p = json.loads(r["payload_json"])
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug("Malformed payload_json for briefing %s: %s", report_id, e)
                    p = {}
            dispatched = []
            if r["dispatched_channels"]:
                try:
                    dispatched = json.loads(r["dispatched_channels"])
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug("Malformed dispatched_channels for briefing %s: %s", report_id, e)
                    dispatched = []
            return {
                "report_id": r["report_id"],
                "user_id": r["user_id"],
                "slot": r["slot"] or p.get("slot", "general"),
                "generated_at": r["generated_at"],
                "executive_summary": r["executive_summary"],
                "message_html": p.get("message", r["executive_summary"]),
                "payload": p,
                "dispatched_channels": dispatched
            }

    def get_recent_briefings(self, limit: int = 10, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if user_id:
                cursor.execute("""
                    SELECT report_id, generated_at, executive_summary, raw_news_count, payload_json, dispatched_channels
                    FROM briefing_history
                    WHERE user_id = ?
                    ORDER BY generated_at DESC
                    LIMIT ?
                """, (user_id, limit))
            else:
                cursor.execute("""
                    SELECT report_id, generated_at, executive_summary, raw_news_count, payload_json, dispatched_channels
                    FROM briefing_history
                    WHERE user_id IS NULL AND slot IS NOT NULL
                    ORDER BY generated_at DESC
                    LIMIT ?
                """, (limit,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                results.append({
                    "report_id": r["report_id"],
                    "generated_at": r["generated_at"],
                    "executive_summary": r["executive_summary"],
                    "raw_news_count": r["raw_news_count"],
                    "payload": json.loads(r["payload_json"]) if r["payload_json"] else {},
                    "dispatched_channels": json.loads(r["dispatched_channels"]) if r["dispatched_channels"] else []
                })
            return results

    def record_feedback(self, target_id: str, feedback_type: str, user_notes: str = ""):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_feedback (target_id, feedback_type, user_notes)
                VALUES (?, ?, ?)
            """, (target_id, feedback_type, user_notes))
            conn.commit()

    def get_feedback_stats(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT feedback_type, COUNT(*) as count
                FROM user_feedback
                GROUP BY feedback_type
            """)
            rows = cursor.fetchall()
            return {r["feedback_type"]: r["count"] for r in rows}

    def set_kv(self, key: str, value: Any):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO kv_store (key, value_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, json.dumps(value, default=str)))
            conn.commit()
        self.backup_to_gcs()


    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value_json FROM kv_store WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row and row["value_json"]:
                return json.loads(row["value_json"])
            return default

    def claim_telegram_update(self, update_id: int) -> bool:
        """
        Atomically claims a Telegram update_id using SQLite INSERT OR IGNORE.
        Returns True if update was claimed (first time seen), False if duplicate or on error (fails closed).
        """
        if not update_id:
            return True
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO processed_updates (update_id) VALUES (?)", (int(update_id),))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to claim telegram update {update_id}: {e}")
            return False

    def record_token_usage(
        self,
        agent_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        model_name: str,
        user_id: Optional[str] = None,
        cached_tokens: int = 0
    ):
        total = prompt_tokens + completion_tokens
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO token_usage (
                    agent_name, prompt_tokens, cached_tokens, completion_tokens, total_tokens, cost_usd, model_name, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (agent_name, prompt_tokens, cached_tokens, completion_tokens, total, cost_usd, model_name, user_id))
            conn.commit()

    def record_thesis_outcome(
        self,
        thesis_id: str,
        user_id: str,
        ticker: str,
        stance: str,
        conviction_pct: float,
        entry_price: float,
        critic_verdict: Optional[str] = None,
        critic_conf_pct: Optional[float] = None
    ) -> None:
        """Records an investment recommendation into the ground truth outcome ledger for forward return tracking (§4A)."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO thesis_outcomes (
                    thesis_id, user_id, ticker, stance, conviction_pct,
                    critic_verdict, critic_conf_pct, entry_price, entry_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (thesis_id, user_id, ticker.upper(), stance, conviction_pct, critic_verdict, critic_conf_pct, entry_price, today))
            conn.commit()

    LEGACY_DEFAULT_PASSWORDS = ("sentinel_admin",)

    def rotate_legacy_admin_credentials(self) -> None:
        """One-time migration: rotates any admin account holding a published legacy default credential.
        Also increments token_epoch to immediately invalidate all pre-existing sessions (R-1).
        """
        from auth.crypto import verify_password, hash_password
        from config import config
        import secrets

        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, username, password_hash, token_epoch FROM users WHERE role = 'admin'")
                rows = cur.fetchall()
        except Exception as e:
            logger.debug(f"rotate_legacy_admin_credentials skipped (table not initialized yet): {e}")
            return

        for row in rows:
            user_dict = dict(row)
            if not any(verify_password(p, user_dict["password_hash"]) for p in self.LEGACY_DEFAULT_PASSWORDS):
                continue

            new_pw = config.dashboard_password
            if not new_pw:
                if os.getenv("K_SERVICE") or os.getenv("ENVIRONMENT") == "production":
                    raise RuntimeError(
                        "CRITICAL SECURITY ALERT: Admin account holds a published default credential ('sentinel_admin') "
                        "and DASHBOARD_PASSWORD is not configured. Refusing to start."
                    )
                new_pw = secrets.token_urlsafe(24)
                logger.warning("Rotated legacy admin credential for %s. Ephemeral passcode: %s", user_dict["username"], new_pw)

            new_epoch = int(user_dict.get("token_epoch") or 1) + 1
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE users SET password_hash = ?, token_epoch = ? WHERE id = ?",
                    (hash_password(new_pw), new_epoch, user_dict["id"]),
                )
                conn.commit()
            logger.critical("SECURITY AUDIT: Successfully rotated legacy default credential for admin user '%s' (token_epoch=%d).", user_dict["username"], new_epoch)


    def get_today_token_usage(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if user_id:
                cursor.execute("""
                    SELECT 
                        COALESCE(SUM(total_tokens), 0) as total_tokens,
                        COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                        COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                        COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
                    FROM token_usage
                    WHERE DATE(created_at) = DATE('now') AND user_id = ?
                """, (user_id,))
            else:
                cursor.execute("""
                    SELECT 
                        COALESCE(SUM(total_tokens), 0) as total_tokens,
                        COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                        COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                        COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
                    FROM token_usage
                    WHERE DATE(created_at) = DATE('now')
                """)
            row = cursor.fetchone()
            if row:
                return {
                    "total_tokens": int(row["total_tokens"]),
                    "prompt_tokens": int(row["prompt_tokens"]),
                    "completion_tokens": int(row["completion_tokens"]),
                    "total_cost_usd": float(row["total_cost_usd"])
                }
            return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_cost_usd": 0.0}


    # -------------------------------------------------------------
    # Multi-User & Account Management
    # -------------------------------------------------------------
    def get_or_create_default_admin(self) -> Dict[str, Any]:
        """Ensures default admin user (@forello0) exists and has encrypted Gemini key."""
        from auth.crypto import hash_password, encrypt_api_key
        from config import config

        tg_user = (config.telegram_allowed_usernames[0] if config.telegram_allowed_usernames else "forello0").lower().replace("@", "")
        tg_chat = config.telegram_chat_id or self.get_kv("telegram_active_chat_id") or ""

        admin = None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE role = 'admin' LIMIT 1")
            row = cursor.fetchone()
            if row:
                admin = dict(row)

            if not admin:
                import secrets
                import uuid

                admin_pass = config.dashboard_password
                if not admin_pass:
                    if os.getenv("K_SERVICE") or os.getenv("ENVIRONMENT") == "production":
                        raise RuntimeError(
                            "CRITICAL SECURITY CONFIGURATION ERROR: DASHBOARD_PASSWORD must be configured in production. "
                            "Refusing to start with insecure or default credentials."
                        )
                    admin_pass = secrets.token_urlsafe(24)
                    logger.warning(
                        "No DASHBOARD_PASSWORD configured in environment. Generated ephemeral admin passcode: %s",
                        admin_pass,
                    )

                pw_hash = hash_password(admin_pass)
                enc_key = encrypt_api_key(config.gemini_api_key) if config.gemini_api_key else ""
                user_id = f"usr_{uuid.uuid4().hex[:12]}"
                cursor.execute("""
                    INSERT INTO users (id, username, email, password_hash, telegram_username, telegram_chat_id, encrypted_gemini_key, role)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'admin')
                """, (user_id, "forello0", "admin@sentinel.internal", pw_hash, tg_user or "forello0", tg_chat, enc_key))
                conn.commit()
                admin = self.get_user_by_id(user_id)


        # Initialize Admin portfolio if empty
        if admin:
            existing_p = self.get_user_portfolio(admin["id"])
            if not existing_p:
                kv_p = self.get_kv("active_portfolio")
                if kv_p and isinstance(kv_p, dict) and kv_p.get("holdings"):
                    self.save_user_portfolio(admin["id"], kv_p, cash=kv_p.get("cash", 10000.0))
                else:
                    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
                    sample_path = os.path.join(data_dir, "sample_portfolio.json")
                    if os.path.exists(sample_path):
                        with open(sample_path, "r") as f:
                            p_data = json.load(f)
                            self.save_user_portfolio(admin["id"], p_data, cash=p_data.get("cash", 10000.0))
        return admin

    def create_user(
        self,
        username: str,
        email: str,
        password: str,
        telegram_username: str = "",
        raw_gemini_key: str = "",
        role: str = "user"
    ) -> Dict[str, Any]:
        import uuid
        from auth.crypto import hash_password, encrypt_api_key

        clean_user = username.strip()
        clean_email = email.strip().lower()
        clean_tg = telegram_username.strip().lower().replace("@", "")
        pw_hash = hash_password(password)
        enc_key = encrypt_api_key(raw_gemini_key) if raw_gemini_key else ""
        user_id = f"usr_{uuid.uuid4().hex[:12]}"

        final_role = "user" if role != "admin" else "admin"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (id, username, email, password_hash, telegram_username, telegram_chat_id, encrypted_gemini_key, role)
                VALUES (?, ?, ?, ?, ?, '', ?, ?)
            """, (user_id, clean_user, clean_email, pw_hash, clean_tg, enc_key, final_role))
            conn.commit()
        self.backup_to_gcs(blocking=True)

        # Seed initial empty portfolio with $0.00 cash and 0 holdings (starts off with nothing)
        default_portfolio = {
            "name": f"{clean_user}'s Portfolio",
            "cash": 0.0,
            "holdings": []
        }
        self.save_user_portfolio(user_id, default_portfolio, cash=0.0)
        return self.get_user_by_id(user_id)

    def authenticate_user(self, username_or_email: str, password: str) -> Optional[Dict[str, Any]]:
        from auth.crypto import verify_password
        clean = username_or_email.strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)
            """, (clean, clean))
            row = cursor.fetchone()
            if not row:
                return None
            user_dict = dict(row)
            if verify_password(password, user_dict["password_hash"]):
                return user_dict
            return None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username.strip(),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_telegram(self, tg_identifier: str) -> Optional[Dict[str, Any]]:
        if not tg_identifier:
            return None
        clean = str(tg_identifier).strip().lower().replace("@", "")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM users 
                WHERE LOWER(telegram_username) = ? OR telegram_chat_id = ?
            """, (clean, clean))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_user_settings(
        self,
        user_id: str,
        telegram_username: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        raw_gemini_key: Optional[str] = None,
        password: Optional[str] = None,
        email: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        from auth.crypto import encrypt_api_key, hash_password
        updates = []
        params = []

        if telegram_username is not None:
            clean_tg = telegram_username.strip().lower().replace("@", "")
            updates.append("telegram_username = ?")
            params.append(clean_tg)

        if telegram_chat_id is not None:
            updates.append("telegram_chat_id = ?")
            params.append(telegram_chat_id.strip())

        if raw_gemini_key is not None:
            enc_key = encrypt_api_key(raw_gemini_key)
            updates.append("encrypted_gemini_key = ?")
            params.append(enc_key)

        if password is not None and password.strip():
            pw_hash = hash_password(password.strip())
            updates.append("password_hash = ?")
            params.append(pw_hash)

        if email is not None and email.strip():
            updates.append("email = ?")
            params.append(email.strip().lower())

        if not updates:
            return self.get_user_by_id(user_id)

        params.append(user_id)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"  # noqa: S608 — fragment is built exclusively from hardcoded schema literals; values are bound
            cursor.execute(query, tuple(params))
            conn.commit()

        self.backup_to_gcs(blocking=True)
        return self.get_user_by_id(user_id)

    def get_user_portfolio(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_portfolios WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row and row["holdings_json"]:
                p_data = json.loads(row["holdings_json"])
                if row["cash"] is not None:
                    p_data["cash"] = float(row["cash"])
                return p_data
            return None

    def save_user_portfolio(self, user_id: str, portfolio_dict: Dict[str, Any], cash: Optional[float] = None) -> None:
        if cash is None:
            cash = float(portfolio_dict.get("cash", 10000.0))
        portfolio_dict["cash"] = cash
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO user_portfolios (user_id, portfolio_name, cash, holdings_json, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                user_id,
                portfolio_dict.get("name", "User Portfolio"),
                cash,
                json.dumps(portfolio_dict, default=str)
            ))
            conn.commit()
        self.backup_to_gcs(blocking=True)

    def record_user_scan(self, user_id: str, report_id: str, payload_dict: Dict[str, Any]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_scans (user_id, report_id, payload_json, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, report_id, json.dumps(payload_dict, default=str)))
            conn.commit()
        self.backup_to_gcs()


    def get_recent_user_scans(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM user_scans 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (user_id, limit))
            rows = cursor.fetchall()
            return [json.loads(r["payload_json"]) for r in rows if r["payload_json"]]

    def get_latest_user_scan(self, user_id: Optional[str] = None) -> Optional[BriefingReport]:
        """Returns the most recent multi-agent scan briefing for the user or None if no scan exists."""
        if not user_id:
            return None

        # 1. Check user-namespaced KV cache
        cached = self.get_kv(f"latest_scan_briefing:{user_id}")
        if cached and isinstance(cached, dict) and "report_id" in cached:
            try:
                return BriefingReport(**cached)
            except Exception as e:
                logger.warning("Failed deserializing cached user scan for %s: %s", user_id, e)

        # 2. Check user_scans table
        user_scans = self.get_recent_user_scans(user_id, limit=1)
        if user_scans and isinstance(user_scans[0], dict):
            try:
                return BriefingReport(**user_scans[0])
            except Exception as e:
                logger.warning("Failed deserializing latest user scan: %s", e)
        return None


    def get_all_active_telegram_users(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM users 
                WHERE telegram_chat_id IS NOT NULL AND telegram_chat_id != ''
            """)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def save_deepdive(
        self,
        user_id: str,
        ticker: str,
        company_name: str,
        current_price: float,
        verdict: str,
        conviction_score: float,
        technicals: Optional[Dict[str, Any]],
        sentiment: Optional[Dict[str, Any]],
        analysis_text: str,
        payload_json: Optional[Dict[str, Any]] = None,
        deepdive_id: Optional[str] = None
    ) -> str:
        """Persists a single-ticker institutional deep dive into the user's repository."""
        import uuid
        record_id = deepdive_id or f"dd_{uuid.uuid4().hex[:10]}"
        full_payload = payload_json or {
            "status": "success",
            "deepdive_id": record_id,
            "ticker": ticker,
            "quote": {"name": company_name, "current_price": current_price},
            "technicals": technicals,
            "sentiment": sentiment,
            "analysis": analysis_text
        }
        full_payload["deepdive_id"] = record_id

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO user_deepdives (
                    id, user_id, ticker, company_name, current_price,
                    verdict, conviction_score, technicals_json, sentiment_json,
                    analysis_text, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                record_id,
                user_id,
                ticker.upper(),
                company_name,
                float(current_price or 0.0),
                verdict,
                float(conviction_score or 85.0),
                json.dumps(technicals, default=str) if technicals else None,
                json.dumps(sentiment, default=str) if sentiment else None,
                analysis_text,
                json.dumps(full_payload, default=str)
            ))
            conn.commit()
        self.backup_to_gcs()
        return record_id

    def get_user_deepdives(
        self,
        user_id: str,
        limit: int = 50,
        ticker: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves list of archived deep dives for user, newest first."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if ticker:
                cursor.execute("""
                    SELECT id, user_id, ticker, company_name, current_price,
                           verdict, conviction_score, created_at
                    FROM user_deepdives
                    WHERE user_id = ? AND ticker = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, ticker.upper(), limit))
            else:
                cursor.execute("""
                    SELECT id, user_id, ticker, company_name, current_price,
                           verdict, conviction_score, created_at
                    FROM user_deepdives
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, limit))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_deepdive_by_id(
        self,
        deepdive_id: str,
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Retrieves the full deep dive record and parsed payload."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if user_id:
                cursor.execute("""
                    SELECT * FROM user_deepdives WHERE id = ? AND user_id = ?
                """, (deepdive_id, user_id))
            else:
                cursor.execute("""
                    SELECT * FROM user_deepdives WHERE id = ?
                """, (deepdive_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("payload_json"):
                try:
                    res["payload"] = json.loads(res["payload_json"])
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug("Malformed payload_json for deepdive %s: %s", deepdive_id, e)
                    res["payload"] = None
            if res.get("technicals_json"):
                try:
                    res["technicals"] = json.loads(res["technicals_json"])
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug("Malformed technicals_json for deepdive %s: %s", deepdive_id, e)
                    res["technicals"] = None
            if res.get("sentiment_json"):
                try:
                    res["sentiment"] = json.loads(res["sentiment_json"])
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug("Malformed sentiment_json for deepdive %s: %s", deepdive_id, e)
                    res["sentiment"] = None
            return res

    def delete_deepdive(self, deepdive_id: str, user_id: str, is_admin: bool = False) -> bool:
        """Deletes an archived deep dive."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if is_admin:
                cursor.execute("""
                    DELETE FROM user_deepdives WHERE id = ?
                """, (deepdive_id,))
            else:
                cursor.execute("""
                    DELETE FROM user_deepdives WHERE id = ? AND user_id = ?
                """, (deepdive_id, user_id))
            deleted = cursor.rowcount > 0
            conn.commit()
        if deleted:
            self.backup_to_gcs()
        return deleted



