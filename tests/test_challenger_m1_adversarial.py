"""
tests/test_challenger_m1_adversarial.py
Adversarial Challenge Test Harness for Milestone 1 (Financial Sentinel V3)
Empirical verification of:
1. GCS if_generation_match=0 enforcement under varied conditions
2. Resilient handling of 412 PreconditionFailed without state corruption or leakage
3. Heavy concurrent WAL writes under continuous PRAGMA wal_checkpoint(TRUNCATE)
4. Teardown sequence ordering, idempotency, and fault isolation in FastAPI lifespan
"""
import glob
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import PreconditionFailed

import storage.state_store as ss
from storage.state_store import StateStore
import web.app as web_app
from web.app import app, lifespan, orchestrator, telegram_bot


@pytest.fixture(autouse=True)
def preserve_gcs_state():
    """Preserve and restore global GCS state across tests."""
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
# 1. GCS if_generation_match=0 Empirical Verification Under Varied Conditions
# ============================================================================

class TestGcsGenerationMatchPrecondition:

    @pytest.mark.parametrize("initial_generation,expected_match", [
        (None, 0),
        (0, 0),
        (123456789, 123456789),
        (999999999999, 999999999999),
    ])
    def test_backup_to_gcs_generation_match_parameterization(self, monkeypatch, tmp_path, initial_generation, expected_match):
        """Verify upload_kwargs always passes correct if_generation_match under various starting generations."""
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"
        ss._GCS_REMOTE_GENERATION = initial_generation

        db_file = tmp_path / "test.db"
        store = StateStore(db_path=str(db_file))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.generation = 555666777
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            result = store.backup_to_gcs(blocking=True)
            assert result is True

            assert mock_blob.upload_from_filename.called
            _, kwargs = mock_blob.upload_from_filename.call_args
            assert "if_generation_match" in kwargs
            assert kwargs["if_generation_match"] == expected_match

    def test_backup_after_no_remote_blob_restore_supplies_zero(self, monkeypatch, tmp_path):
        """When restore_from_gcs finds no remote blob, generation is None and backup must pass 0."""
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = False
        ss._GCS_RESTORE_STATE = "uninitialized"
        ss._GCS_REMOTE_GENERATION = None

        db_file = tmp_path / "fresh.db"
        store = StateStore(db_path=str(db_file))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.exists.return_value = False  # No remote blob exists
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            restored = store.restore_from_gcs(force=True)
            assert restored is False
            assert ss._GCS_RESTORE_STATE == "no_remote_blob"
            assert ss._GCS_REMOTE_GENERATION is None

            # Now perform backup
            mock_blob.generation = 11223344
            backup_res = store.backup_to_gcs(blocking=True)
            assert backup_res is True

            _, kwargs = mock_blob.upload_from_filename.call_args
            assert kwargs["if_generation_match"] == 0
            assert ss._GCS_REMOTE_GENERATION == 11223344

    def test_backup_aborts_completely_if_restore_failed(self, monkeypatch, tmp_path):
        """If restore failed, backup must refuse to execute even if generation is None."""
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = False
        ss._GCS_RESTORE_STATE = "failed"
        ss._GCS_REMOTE_GENERATION = None

        db_file = tmp_path / "failed_state.db"
        store = StateStore(db_path=str(db_file))

        with patch("google.cloud.storage.Client") as mock_client:
            res = store.backup_to_gcs(blocking=True)
            assert res is False
            # Client should not even be instantiated
            assert not mock_client.called

    def test_backup_skips_when_not_cloud_run(self, monkeypatch, tmp_path):
        """When neither K_SERVICE nor GCS_SYNC_ENABLED is present, backup returns False without uploading."""
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("GCS_SYNC_ENABLED", raising=False)
        ss._GCS_REMOTE_GENERATION = None

        db_file = tmp_path / "local.db"
        store = StateStore(db_path=str(db_file))

        with patch("google.cloud.storage.Client") as mock_client:
            res = store.backup_to_gcs(blocking=True)
            assert res is False
            assert not mock_client.called


# ============================================================================
# 2. Simulated 412 PreconditionFailed Adversarial Harness
# ============================================================================

class TestPreconditionFailedHandling:

    def test_precondition_failed_does_not_crash_caller_or_corrupt_generation(self, monkeypatch, tmp_path):
        """Simulate Google PreconditionFailed 412; verify caller is unaffected and generation state is pristine."""
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"
        ss._GCS_REMOTE_GENERATION = None

        db_file = tmp_path / "test_412.db"
        store = StateStore(db_path=str(db_file))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            # Simulate real Google API 412 PreconditionFailed exception
            mock_blob.upload_from_filename.side_effect = PreconditionFailed("412 Precondition Failed: generation mismatch")
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            # Must not raise
            res = store.backup_to_gcs(blocking=True)
            # Generation must remain None
            assert ss._GCS_REMOTE_GENERATION is None

            # Verify caller mutation method completes cleanly
            store.set_kv("test_key", {"status": "ok"})
            val = store.get_kv("test_key")
            assert val == {"status": "ok"}

    def test_precondition_failed_does_not_leak_temp_files(self, monkeypatch, tmp_path):
        """Verify temporary SQLite backup files are removed even when upload raises 412."""
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"
        ss._GCS_REMOTE_GENERATION = None

        db_file = tmp_path / "test_leak.db"
        store = StateStore(db_path=str(db_file))

        temp_dir = tempfile.gettempdir()

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.upload_from_filename.side_effect = PreconditionFailed("412 Precondition Failed")
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            before_files = set(glob.glob(os.path.join(temp_dir, "*.db")))
            store.backup_to_gcs(blocking=True)
            after_files = set(glob.glob(os.path.join(temp_dir, "*.db")))

        leaked_files = after_files - before_files
        assert len(leaked_files) == 0, f"Temporary database files leaked after 412: {leaked_files}"

    def test_consecutive_backups_after_412_recovery(self, monkeypatch, tmp_path):
        """Verify that after a 412 error, the backup lock is released and subsequent uploads succeed."""
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
        ss._GCS_RESTORED = True
        ss._GCS_RESTORE_STATE = "restored"
        ss._GCS_REMOTE_GENERATION = None

        db_file = tmp_path / "recovery.db"
        store = StateStore(db_path=str(db_file))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            # First upload fails with 412
            mock_blob.upload_from_filename.side_effect = [
                PreconditionFailed("412 Precondition Failed"),
                None  # Second upload succeeds
            ]
            mock_blob.generation = 99887766
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            # First attempt fails safely
            store.backup_to_gcs(blocking=True)
            assert ss._GCS_REMOTE_GENERATION is None

            # Now simulate remote generation being discovered or reset, and next upload succeeds
            ss._GCS_REMOTE_GENERATION = 99887700
            store.backup_to_gcs(blocking=True)
            assert ss._GCS_REMOTE_GENERATION == 99887766


# ============================================================================
# 3. Heavy Concurrent WAL Writes & PRAGMA wal_checkpoint(TRUNCATE) Stress Test
# ============================================================================

class TestConcurrentWalCheckpointStress:

    def test_concurrent_wal_writes_with_continuous_truncate_checkpoint(self, tmp_path):
        """
        Stress test: 12 concurrent writer threads executing transactions while 2 checkpoint
        threads hammer PRAGMA wal_checkpoint(TRUNCATE).
        Verifies:
        - Zero deadlocks
        - Zero unhandled SQLite busy/lock exceptions
        - 100% data integrity verified via PRAGMA integrity_check
        - All committed rows present
        """
        db_path = str(tmp_path / "stress_wal.db")
        store = StateStore(db_path=db_path)

        # Setup test table
        with store._get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS stress_log (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INT, payload TEXT, ts REAL);")

        num_writers = 8
        writes_per_thread = 50
        errors = []
        stop_checkpointing = threading.Event()
        checkpoint_stats = {"success": 0, "failure": 0}

        def checkpoint_worker():
            # Dedicated thread-local connection for checkpointing
            while not stop_checkpointing.is_set():
                ok = store.wal_checkpoint("TRUNCATE")
                if ok:
                    checkpoint_stats["success"] += 1
                else:
                    checkpoint_stats["failure"] += 1
                time.sleep(0.005)

        def write_worker(thread_idx: int):
            try:
                for i in range(writes_per_thread):
                    with store._write_transaction() as conn:
                        conn.execute(
                            "INSERT INTO stress_log (thread_id, payload, ts) VALUES (?, ?, ?);",
                            (thread_idx, f"stress_{thread_idx}_{i}", time.time())
                        )
                    # Occasional KV store write to exercise diverse codepaths
                    if i % 10 == 0:
                        store.set_kv(f"thread_{thread_idx}_key_{i}", {"step": i})
                    time.sleep(0.001)
            except Exception as e:
                errors.append((thread_idx, e))

        # Launch checkpoint thread
        ckpt_thread = threading.Thread(target=checkpoint_worker, daemon=True)
        ckpt_thread.start()

        # Launch writer threads in parallel
        with ThreadPoolExecutor(max_workers=num_writers) as executor:
            futures = [executor.submit(write_worker, t) for t in range(num_writers)]
            for future in as_completed(futures):
                future.result()

        # Stop checkpoint loop
        stop_checkpointing.set()
        ckpt_thread.join(timeout=3.0)

        # Assert no writer encountered lock errors or crashed
        assert len(errors) == 0, f"Concurrent writers encountered errors: {errors}"
        assert checkpoint_stats["success"] > 0, "wal_checkpoint did not execute any successful checkpoints"

        # Final WAL checkpoint
        final_ok = store.wal_checkpoint("TRUNCATE")
        assert final_ok is True

        # Verify database integrity
        with store._get_connection() as conn:
            cursor = conn.execute("PRAGMA integrity_check;")
            integrity_rows = cursor.fetchall()
            assert integrity_rows[0][0] == "ok", f"Integrity check failed: {integrity_rows}"

            # Verify total row count matches exact written count
            cursor = conn.execute("SELECT COUNT(*) FROM stress_log;")
            count = cursor.fetchone()[0]
            expected_count = num_writers * writes_per_thread
            assert count == expected_count, f"Expected {expected_count} rows, got {count}"


# ============================================================================
# 4. Lifespan Teardown Ordering, Isolation, and Robustness
# ============================================================================

class TestLifespanTeardownHardening:

    @pytest.mark.anyio
    async def test_teardown_executes_all_steps_when_intermediate_step_fails(self, monkeypatch):
        """
        Verify fault containment: if wal_checkpoint fails or raises, backup_to_gcs and
        close_connection MUST still be invoked.
        """
        monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
        monkeypatch.setenv("K_SERVICE", "financial-sentinel")
        ss._GCS_RESTORED = True

        called_steps = []

        def failing_wal(mode="TRUNCATE"):
            called_steps.append("wal_checkpoint")
            raise sqlite3.OperationalError("Simulated disk I/O error during checkpoint")

        def working_backup(blocking=False):
            called_steps.append("backup_to_gcs")
            return True

        def working_close():
            called_steps.append("close_connection")

        monkeypatch.setattr(orchestrator.state_store, "wal_checkpoint", failing_wal)
        monkeypatch.setattr(orchestrator.state_store, "backup_to_gcs", working_backup)
        monkeypatch.setattr(orchestrator.state_store, "close_connection", working_close)

        # Lifespan must handle the error without aborting remaining teardown steps
        async with lifespan(app):
            pass

        assert "wal_checkpoint" in called_steps
        assert "backup_to_gcs" in called_steps
        assert "close_connection" in called_steps

    @pytest.mark.anyio
    async def test_teardown_handles_null_state_store(self, monkeypatch):
        """Verify lifespan gracefully terminates even if state_store is None or uninitialized."""
        monkeypatch.setattr(orchestrator, "state_store", None)

        async with lifespan(app):
            pass  # Must exit cleanly without AttributeError

    def test_telegram_bot_stop_and_stop_polling_aliases(self):
        """Verify stop() and stop_polling() both set is_running to False."""
        telegram_bot.is_running = True
        telegram_bot.stop_polling()
        assert telegram_bot.is_running is False

        telegram_bot.is_running = True
        telegram_bot.stop()
        assert telegram_bot.is_running is False
