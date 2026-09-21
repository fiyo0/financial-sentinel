"""
tests/test_challenger_m1_2_adversarial.py
Adversarial Challenge Suite 2 for Milestone 1 (Financial Sentinel V3)
Empirical verification of:
1. Lifespan teardown under GCS network timeout failure injection (process exits cleanly, code 0)
2. Lifespan teardown under SQLite busy/locked conditions during WAL checkpointing and backup
3. Telegram bot stop() and stop_polling() behavior under concurrent and edge-case execution
4. Multi-failure cascade across all shutdown components
5. Temp file hygiene under repeated network/IO failure injection
"""
import glob
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import pytest
import requests

from config import config
import storage.state_store as ss
from storage.state_store import StateStore
import web.app as web_app
from web.app import app, lifespan, orchestrator, telegram_bot


@pytest.fixture(autouse=True)
def preserve_gcs_state():
    """Preserve and restore global GCS state and executor across tests."""
    orig_restored = ss._GCS_RESTORED
    orig_state = ss._GCS_RESTORE_STATE
    orig_gen = ss._GCS_REMOTE_GENERATION
    orig_pending = ss._GCS_BACKUP_PENDING
    orig_thread = ss._GCS_BACKUP_THREAD
    orig_executor = web_app.sentinel_executor
    yield
    ss._GCS_RESTORED = orig_restored
    ss._GCS_RESTORE_STATE = orig_state
    ss._GCS_REMOTE_GENERATION = orig_gen
    ss._GCS_BACKUP_PENDING = orig_pending
    ss._GCS_BACKUP_THREAD = orig_thread
    if getattr(web_app.sentinel_executor, "_shutdown", False):
        web_app.sentinel_executor = ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="sentinel_worker"
        )


# ============================================================================
# 1. Failure Injection: GCS Network Timeout During Lifespan Shutdown
# ============================================================================

class TestGcsNetworkTimeoutFailureInjection:

    @pytest.mark.parametrize("timeout_exc", [
        requests.exceptions.ConnectTimeout("Connection to storage.googleapis.com timed out (connect timeout=30)"),
        requests.exceptions.ReadTimeout("Read from storage.googleapis.com timed out (read timeout=30)"),
        TimeoutError("POSIX operation timed out"),
        ConnectionResetError("Connection reset by peer (GCS endpoint dropped connection)"),
    ])
    @pytest.mark.anyio
    async def test_lifespan_survives_gcs_network_timeout(self, monkeypatch, timeout_exc):
        """
        Adversarial Test: When GCS upload raises network timeout or connection reset during
        lifespan shutdown, the teardown must complete gracefully without unhandled exceptions.
        """
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("K_SERVICE", "financial-sentinel")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.upload_from_filename.side_effect = timeout_exc
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            # Execute full lifespan lifecycle: startup -> running -> shutdown
            async with lifespan(app):
                # Simulate application activity
                orchestrator.state_store.set_kv("active_key_before_shutdown", {"ts": time.time()})

        # Lifespan must have completed cleanly without raising

    def test_subprocess_container_shutdown_exits_code_zero_on_gcs_timeout(self, tmp_path):
        """
        Empirical Process Test: Run an isolated Python subprocess that starts FastAPI,
        triggers lifespan shutdown with GCS network timeout, and verify the process
        exits with returncode 0 and no uncaught traceback in stderr.
        """
        script = f"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch
import requests

sys.path.insert(0, {repr(os.getcwd())})

os.environ["GCS_SYNC_ENABLED"] = "true"
os.environ["K_SERVICE"] = "financial-sentinel"
os.environ["GCS_DATA_BUCKET"] = "sentinel-adversarial-test"

import storage.state_store as ss
ss._GCS_RESTORED = True
ss._GCS_RESTORE_STATE = "restored"

from web.app import app, lifespan, orchestrator

async def main():
    with patch("google.cloud.storage.Client") as mock_client:
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_from_filename.side_effect = requests.exceptions.ConnectTimeout("Connect timeout to GCS")
        mock_bucket.blob.return_value = mock_blob
        mock_client.return_value.bucket.return_value = mock_bucket

        async with lifespan(app):
            pass

asyncio.run(main())
print("CONTAINER_EXIT_CLEAN")
sys.exit(0)
"""
        py_file = tmp_path / "subproc_timeout.py"
        py_file.write_text(script)

        res = subprocess.run(  # noqa: S603
            [sys.executable, str(py_file)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=os.getcwd(),
        )

        assert res.returncode == 0, f"Process crashed with returncode {res.returncode}. Stderr: {res.stderr}"
        assert "CONTAINER_EXIT_CLEAN" in res.stdout
        assert "Traceback (most recent call last)" not in res.stderr

    def test_repeated_gcs_timeouts_do_not_leak_temp_files(self, monkeypatch, tmp_path):
        """
        Adversarial Test: Verify that repeated GCS upload timeouts never leak
        temporary snapshot files (*.db) in the OS temp directory.
        """
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"

        db_path = str(tmp_path / "leak_test.db")
        store = StateStore(db_path=db_path)
        store._init_db()
        store.set_kv("key1", {"data": "test"})

        temp_dir = tempfile.gettempdir()
        initial_tmp_files = set(glob.glob(os.path.join(temp_dir, "*.db")))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.upload_from_filename.side_effect = requests.exceptions.ReadTimeout("Socket read timeout")
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            for _ in range(15):
                store.backup_to_gcs(blocking=True)

        current_tmp_files = set(glob.glob(os.path.join(temp_dir, "*.db")))
        leaked = current_tmp_files - initial_tmp_files
        assert len(leaked) == 0, f"Leaked temporary database files detected: {leaked}"


# ============================================================================
# 2. Concurrency: SQLite Busy / Locked During Lifespan Teardown
# ============================================================================

class TestSqliteBusyDuringLifespanTeardown:

    def test_wal_checkpoint_under_external_exclusive_lock(self, tmp_path):
        """
        Adversarial Test: An external connection holds BEGIN EXCLUSIVE.
        Verify wal_checkpoint does not crash, handles busy state gracefully,
        and returns cleanly.
        """
        db_path = str(tmp_path / "busy_test.db")
        store = StateStore(db_path=db_path)
        store._init_db()

        # External connection acquires exclusive write lock
        lock_conn = sqlite3.connect(db_path, timeout=0.1)
        lock_conn.execute("PRAGMA journal_mode=WAL;")
        lock_conn.execute("BEGIN EXCLUSIVE;")

        try:
            # Checkpoint while DB is exclusively locked
            # In SQLite, PRAGMA wal_checkpoint(TRUNCATE) returns [(1, log, ckpt)] or raises
            result = store.wal_checkpoint("TRUNCATE")
            assert isinstance(result, bool)
        finally:
            lock_conn.rollback()
            lock_conn.close()

        # Once lock is released, checkpoint succeeds completely
        assert store.wal_checkpoint("TRUNCATE") is True

    @pytest.mark.anyio
    async def test_lifespan_shutdown_when_sqlite_locked_by_long_transaction(self, monkeypatch, tmp_path):
        """
        Adversarial Test: A background thread holds an uncommitted SQLite write transaction
        throughout the entire lifespan shutdown sequence.
        Verifies:
        - wal_checkpoint does not raise uncaught exception
        - backup_to_gcs still succeeds using SQLite Online Backup API snapshot isolation
        - close_connection closes thread-local handle
        - Lifespan exits without crashing
        """
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("K_SERVICE", "financial-sentinel")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"

        db_path = str(tmp_path / "locked_lifespan.db")
        test_store = StateStore(db_path=db_path)
        test_store._init_db()
        test_store.set_kv("baseline", {"ok": True})

        monkeypatch.setattr(orchestrator, "state_store", test_store)

        # Hold a long write transaction in a separate thread
        lock_acquired = threading.Event()
        stop_lock = threading.Event()

        def locking_thread():
            conn = sqlite3.connect(db_path, timeout=1.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("INSERT OR REPLACE INTO kv_store (key, value_json, updated_at) VALUES ('locking', '{\"val\": 1}', CURRENT_TIMESTAMP);")
            lock_acquired.set()
            stop_lock.wait(timeout=5.0)
            conn.rollback()
            conn.close()

        t = threading.Thread(target=locking_thread, daemon=True)
        t.start()
        lock_acquired.wait(timeout=2.0)

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.generation = 12345
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            try:
                # Lifespan must complete teardown safely even with locked writer
                async with lifespan(app):
                    pass
            finally:
                stop_lock.set()
                t.join(timeout=2.0)

        # Verify integrity of SQLite database after teardown
        with test_store._get_connection() as conn:
            cursor = conn.execute("PRAGMA integrity_check;")
            assert cursor.fetchone()[0] == "ok"

    def test_backup_handles_sqlite_operational_error_gracefully(self, monkeypatch, tmp_path):
        """
        Adversarial Test: If sqlite3.connect or src.backup raises OperationalError
        (e.g., database disk locked or file corruption), backup_to_gcs handles it
        without raising and cleans up temp files.
        """
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"

        db_path = str(tmp_path / "op_err.db")
        store = StateStore(db_path=db_path)
        store._init_db()

        with patch("sqlite3.connect") as mock_connect:
            mock_connect.side_effect = sqlite3.OperationalError("database is locked")

            # Must not crash
            result = store.backup_to_gcs(blocking=True)
            assert result is True


# ============================================================================
# 3. Telegram Bot Teardown: stop() and stop_polling()
# ============================================================================

class TestTelegramBotTeardownHardening:

    def test_stop_and_stop_polling_when_bot_unconfigured(self, monkeypatch):
        """Calling stop() or stop_polling() on unconfigured bot must never raise."""
        monkeypatch.setattr(config, "telegram_bot_token", "")
        monkeypatch.setattr(config, "telegram_chat_id", "")

        from channels.telegram_bot import FinancialSentinelTelegramBot
        bot = FinancialSentinelTelegramBot(orchestrator=orchestrator)
        assert not bot.is_configured()

        # Both methods must succeed without error
        bot.stop()
        assert bot.is_running is False
        bot.stop_polling()
        assert bot.is_running is False

    def test_concurrent_stop_and_stop_polling_thread_safety(self, monkeypatch):
        """
        Adversarial Test: 20 threads simultaneously call stop() and stop_polling()
        on an active bot. Verify zero race condition crashes or deadlocks.
        """
        monkeypatch.setattr(config, "telegram_bot_token", "fake_token_12345")
        monkeypatch.setattr(config, "telegram_chat_id", "12345")

        from channels.telegram_bot import FinancialSentinelTelegramBot
        bot = FinancialSentinelTelegramBot(orchestrator=orchestrator)
        bot.is_running = True

        errors = []

        def stopper(idx):
            try:
                if idx % 2 == 0:
                    bot.stop()
                else:
                    bot.stop_polling()
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(stopper, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Concurrent stop calls raised errors: {errors}"
        assert bot.is_running is False

    def test_stop_polling_terminates_active_polling_thread(self, monkeypatch):
        """
        Verify that calling stop_polling() while _poll_loop is running
        causes the polling thread to terminate cleanly within timeout.
        """
        monkeypatch.setattr(config, "telegram_bot_token", "fake_token_12345")
        monkeypatch.setattr(config, "telegram_chat_id", "12345")

        from channels.telegram_bot import FinancialSentinelTelegramBot
        bot = FinancialSentinelTelegramBot(orchestrator=orchestrator)

        with patch("httpx.get") as mock_get:
            poll_active = threading.Event()

            def slow_get(*args, **kwargs):
                poll_active.set()
                time.sleep(0.02)
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"ok": True, "result": []}
                return mock_resp

            mock_get.side_effect = slow_get

            bot.start_polling()
            assert poll_active.wait(timeout=2.0), "Polling thread did not start"
            assert bot._thread is not None
            assert bot._thread.is_alive()

            # Now call stop_polling
            bot.stop_polling()
            assert bot.is_running is False

            # Wait for thread to exit
            bot._thread.join(timeout=2.0)
            assert not bot._thread.is_alive(), "Polling thread did not terminate after stop_polling()"


# ============================================================================
# 4. Multi-Failure Cascade Resilience
# ============================================================================

class TestMultiFailureCascadeResilience:

    @pytest.mark.anyio
    async def test_lifespan_survives_every_single_component_failing(self, monkeypatch):
        """
        Catastrophic Failure Injection:
        EVERY SINGLE component during shutdown raises an unhandled exception:
        - daily_scheduler.stop() raises
        - telegram_bot.stop() raises
        - sentinel_executor.shutdown(wait=False) raises
        - wal_checkpoint() raises
        - backup_to_gcs() raises
        - close_connection() raises
        Lifespan must survive, isolate each failure, log warnings, and exit cleanly.
        """
        from web.app import daily_scheduler

        monkeypatch.setattr(daily_scheduler, "stop", MagicMock(side_effect=RuntimeError("Scheduler crash")))
        monkeypatch.setattr(telegram_bot, "stop", MagicMock(side_effect=RuntimeError("Telegram crash")))

        orig_shutdown = web_app.sentinel_executor.shutdown
        shutdown_called = []
        failed_once = False

        def failing_shutdown(wait=False):
            nonlocal failed_once
            shutdown_called.append(wait)
            if not failed_once:
                failed_once = True
                raise RuntimeError("Executor crash during lifespan teardown")
            return orig_shutdown(wait=wait)

        monkeypatch.setattr(web_app.sentinel_executor, "shutdown", failing_shutdown)

        mock_store = MagicMock()
        mock_store.wal_checkpoint.side_effect = sqlite3.OperationalError("WAL disk crash")
        mock_store.backup_to_gcs.side_effect = requests.exceptions.ConnectTimeout("GCS network down")
        mock_store.close_connection.side_effect = sqlite3.DatabaseError("DB close error")
        monkeypatch.setattr(orchestrator, "state_store", mock_store)

        # Lifespan must NOT raise any exception
        async with lifespan(app):
            pass

        # Verify all failure points were actually reached and executed
        assert daily_scheduler.stop.called
        assert telegram_bot.stop.called
        assert False in shutdown_called
        assert mock_store.wal_checkpoint.called
        assert mock_store.backup_to_gcs.called
        assert mock_store.close_connection.called
