"""
tests/test_v3_state_persistence.py
Regression tests for Requirement R1 (P0):
- GCS if_generation_match=0 precondition when _GCS_REMOTE_GENERATION is None
- GCS if_generation_match=generation precondition when generation is known
- Lifespan SIGTERM teardown executing WAL checkpoint (TRUNCATE), blocking GCS backup, and connection closure
"""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
import pytest

import storage.state_store as ss
from web.app import app, lifespan, orchestrator, telegram_bot
import web.app as web_app


@pytest.fixture(autouse=True)
def reset_gcs_globals():
    orig_restored = ss._GCS_RESTORED
    orig_state = ss._GCS_RESTORE_STATE
    orig_gen = ss._GCS_REMOTE_GENERATION
    orig_executor = web_app.sentinel_executor
    yield
    ss._GCS_RESTORED = orig_restored
    ss._GCS_RESTORE_STATE = orig_state
    ss._GCS_REMOTE_GENERATION = orig_gen
    if getattr(web_app.sentinel_executor, "_shutdown", False):
        web_app.sentinel_executor = ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="sentinel_worker"
        )


def test_gcs_backup_uses_generation_zero_when_remote_generation_is_none(monkeypatch):
    """
    R1.1: When _GCS_REMOTE_GENERATION is None, backup_to_gcs MUST pass if_generation_match=0
    to prevent an uninitialized container from overwriting existing remote state.
    """
    monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
    monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
    ss._GCS_RESTORED = True
    ss._GCS_RESTORE_STATE = "no_remote_blob"
    ss._GCS_REMOTE_GENERATION = None

    store = orchestrator.state_store

    with patch("google.cloud.storage.Client") as mock_client:
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.generation = 88776655
        mock_bucket.blob.return_value = mock_blob
        mock_client.return_value.bucket.return_value = mock_bucket

        success = store.backup_to_gcs(blocking=True)
        assert success is True

        assert mock_blob.upload_from_filename.called
        _, kwargs = mock_blob.upload_from_filename.call_args
        assert "if_generation_match" in kwargs, "Upload must specify if_generation_match"
        assert kwargs["if_generation_match"] == 0, "if_generation_match must be 0 when generation is None"

        # Verify generation is updated upon successful upload
        assert ss._GCS_REMOTE_GENERATION == 88776655


def test_gcs_backup_preserves_existing_generation_precondition(monkeypatch):
    """
    R1.2: When _GCS_REMOTE_GENERATION is a known integer, backup_to_gcs MUST pass
    that generation as if_generation_match to enforce optimistic concurrency.
    """
    monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
    monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
    ss._GCS_RESTORED = True
    ss._GCS_RESTORE_STATE = "restored"
    ss._GCS_REMOTE_GENERATION = 44556677

    store = orchestrator.state_store

    with patch("google.cloud.storage.Client") as mock_client:
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.generation = 44556688
        mock_bucket.blob.return_value = mock_blob
        mock_client.return_value.bucket.return_value = mock_bucket

        success = store.backup_to_gcs(blocking=True)
        assert success is True

        _, kwargs = mock_blob.upload_from_filename.call_args
        assert kwargs["if_generation_match"] == 44556677
        assert ss._GCS_REMOTE_GENERATION == 44556688


def test_gcs_backup_precondition_failed_does_not_corrupt_state(monkeypatch):
    """
    R1.3: If GCS raises PreconditionFailed (412) during upload, backup_to_gcs handles
    the error safely and does not corrupt _GCS_REMOTE_GENERATION.
    """
    monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
    monkeypatch.setenv("GCS_DATA_BUCKET", "test-bucket")
    ss._GCS_RESTORED = True
    ss._GCS_RESTORE_STATE = "no_remote_blob"
    ss._GCS_REMOTE_GENERATION = None

    store = orchestrator.state_store

    with patch("google.cloud.storage.Client") as mock_client:
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_from_filename.side_effect = Exception("412 Precondition Failed")
        mock_bucket.blob.return_value = mock_blob
        mock_client.return_value.bucket.return_value = mock_bucket

        store.backup_to_gcs(blocking=True)

        # Generation should not be updated to an arbitrary value on failure
        assert ss._GCS_REMOTE_GENERATION is None


@pytest.mark.anyio
async def test_lifespan_shutdown_executes_wal_checkpoint_and_sync_backup(monkeypatch):
    """
    R1.4: On container shutdown (SIGTERM), lifespan MUST execute:
    1. PRAGMA wal_checkpoint(TRUNCATE)
    2. orchestrator.state_store.backup_to_gcs(blocking=True)
    3. orchestrator.state_store.close_connection()
    """
    monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
    monkeypatch.setenv("K_SERVICE", "financial-sentinel")
    ss._GCS_RESTORED = True
    ss._GCS_RESTORE_STATE = "restored"

    call_order = []

    orig_wal = orchestrator.state_store.wal_checkpoint
    orig_backup = orchestrator.state_store.backup_to_gcs
    orig_close = orchestrator.state_store.close_connection

    def spy_wal(mode="TRUNCATE"):
        call_order.append(("wal_checkpoint", mode))
        return orig_wal(mode)

    def spy_backup(blocking=False):
        call_order.append(("backup_to_gcs", blocking))
        return True

    def spy_close():
        call_order.append(("close_connection",))
        return orig_close()

    monkeypatch.setattr(orchestrator.state_store, "wal_checkpoint", spy_wal)
    monkeypatch.setattr(orchestrator.state_store, "backup_to_gcs", spy_backup)
    monkeypatch.setattr(orchestrator.state_store, "close_connection", spy_close)

    # Verify telegram_bot.stop_polling alias functions correctly
    telegram_bot.is_running = True
    telegram_bot.stop_polling()
    assert telegram_bot.is_running is False

    async with lifespan(app):
        # Insert test mutation during running application
        orchestrator.state_store.set_kv("lifespan_test_key", {"active": True})

    # Assert sequence of teardown actions
    assert ("wal_checkpoint", "TRUNCATE") in call_order, "wal_checkpoint(TRUNCATE) was not called in lifespan teardown"
    assert ("backup_to_gcs", True) in call_order, "backup_to_gcs(blocking=True) was not called in lifespan teardown"
    assert ("close_connection",) in call_order, "close_connection() was not called in lifespan teardown"

    # Verify order: wal_checkpoint -> backup_to_gcs -> close_connection
    idx_wal = call_order.index(("wal_checkpoint", "TRUNCATE"))
    idx_backup = call_order.index(("backup_to_gcs", True))
    idx_close = call_order.index(("close_connection",))
    assert idx_wal < idx_backup < idx_close, "Teardown order must be wal_checkpoint -> backup_to_gcs -> close_connection"


@pytest.mark.anyio
async def test_lifespan_teardown_handles_failures_gracefully(monkeypatch):
    """
    R1.5: Lifespan shutdown must not crash or leak exceptions even if backup_to_gcs fails.
    """
    monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
    monkeypatch.setenv("K_SERVICE", "financial-sentinel")
    ss._GCS_RESTORED = True

    def failing_checkpoint(mode="TRUNCATE"):
        raise RuntimeError("SQLite WAL checkpoint failed due to disk I/O")

    def failing_backup(blocking=False):
        raise RuntimeError("GCS upload failed due to network timeout")

    def failing_close():
        raise RuntimeError("Database close failed")

    monkeypatch.setattr(orchestrator.state_store, "wal_checkpoint", failing_checkpoint)
    monkeypatch.setattr(orchestrator.state_store, "backup_to_gcs", failing_backup)
    monkeypatch.setattr(orchestrator.state_store, "close_connection", failing_close)

    # Lifespan must exit cleanly without raising unhandled exception
    async with lifespan(app):
        pass
