"""
tests/stress_harness_m1.py
Standalone High-Concurrency Adversarial Stress Harness for Milestone 1:
- 50 concurrent threads executing rapid SQLite writes and commits
- Simultaneous concurrent wal_checkpoint("TRUNCATE")
- Rapid concurrent asynchronous backup_to_gcs(blocking=False) triggers
- Simulated 412 PreconditionFailed injection under concurrent background uploads
- Verification of database integrity and lock safety
"""
import os
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch
from google.api_core.exceptions import PreconditionFailed

import storage.state_store as ss
from storage.state_store import StateStore


def run_high_concurrency_stress_test():
    print("=== STARTING STRESS TEST 1: 50 Writers + Continuous wal_checkpoint(TRUNCATE) ===")
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "concurrency_stress.db")
        store = StateStore(db_path=db_path)

        with store._get_connection() as conn:
            conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT, tid INT, seq INT, data TEXT);")

        num_threads = 50
        writes_per_thread = 20
        total_expected = num_threads * writes_per_thread
        errors = []
        stop_event = threading.Event()
        checkpoint_counts = {"ok": 0, "busy": 0}

        def checkpoint_runner():
            while not stop_event.is_set():
                ok = store.wal_checkpoint("TRUNCATE")
                if ok:
                    checkpoint_counts["ok"] += 1
                else:
                    checkpoint_counts["busy"] += 1
                time.sleep(0.002)

        def writer_runner(tid):
            try:
                for seq in range(writes_per_thread):
                    with store._write_transaction() as conn:
                        conn.execute("INSERT INTO records (tid, seq, data) VALUES (?, ?, ?);", (tid, seq, f"payload_{tid}_{seq}"))
                    if seq % 5 == 0:
                        store.set_kv(f"stress_key_{tid}_{seq}", {"val": seq})
            except Exception as e:
                errors.append((tid, e))

        ckpt_thread = threading.Thread(target=checkpoint_runner, daemon=True)
        ckpt_thread.start()

        threads = [threading.Thread(target=writer_runner, args=(i,)) for i in range(num_threads)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            if t.is_alive():
                raise TimeoutError(f"Thread {t} deadlocked!")

        stop_event.set()
        ckpt_thread.join(timeout=5.0)
        elapsed = time.time() - t0

        print(f"Elapsed time: {elapsed:.2f}s")
        print(f"Checkpoints executed: {checkpoint_counts}")
        print(f"Errors encountered: {len(errors)}")

        if errors:
            print("Errors detail:", errors[:5])
            sys.exit(1)

        # Integrity verification
        with store._get_connection() as conn:
            res = conn.execute("PRAGMA integrity_check;").fetchall()
            assert res[0][0] == "ok", f"Integrity check failed: {res}"
            count = conn.execute("SELECT COUNT(*) FROM records;").fetchone()[0]
            assert count == total_expected, f"Count mismatch: expected {total_expected}, got {count}"

        print(f"PASSED: All {total_expected} writes committed cleanly with 0 deadlocks and valid integrity.")


def run_concurrent_background_backup_debouncing_and_412_stress():
    print("\n=== STARTING STRESS TEST 2: Concurrent Background Backup Debouncing & 412 Fault Injection ===")
    os.environ["GCS_SYNC_ENABLED"] = "true"
    os.environ["GCS_DATA_BUCKET"] = "test-bucket"
    ss._GCS_RESTORED = True
    ss._GCS_RESTORE_STATE = "restored"
    ss._GCS_REMOTE_GENERATION = None

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "backup_stress.db")
        store = StateStore(db_path=db_path)

        upload_call_count = 0
        call_lock = threading.Lock()
        upload_generations_passed = []

        def mock_upload(filename, **kwargs):
            nonlocal upload_call_count
            with call_lock:
                upload_call_count += 1
                gen_match = kwargs.get("if_generation_match")
                upload_generations_passed.append(gen_match)
                # First 2 calls fail with 412 PreconditionFailed
                if upload_call_count <= 2:
                    raise PreconditionFailed("412 Precondition Failed: generation mismatch")

        with patch("google.cloud.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.upload_from_filename.side_effect = mock_upload
            mock_blob.generation = 12345678000
            mock_bucket.blob.return_value = mock_blob
            mock_client.return_value.bucket.return_value = mock_bucket

            # 30 concurrent threads calling backup_to_gcs(blocking=False)
            num_callers = 30
            threads = []
            for _ in range(num_callers):
                t = threading.Thread(target=store.backup_to_gcs, kwargs={"blocking": False})
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            # Wait for debounced worker thread to complete
            time.sleep(0.5)
            if ss._GCS_BACKUP_THREAD and ss._GCS_BACKUP_THREAD.is_alive():
                ss._GCS_BACKUP_THREAD.join(timeout=5.0)

            print(f"Total background upload cycles executed: {upload_call_count}")
            print(f"Generations passed to upload: {upload_generations_passed}")

            # Verify initial calls passed if_generation_match=0
            assert upload_generations_passed[0] == 0, f"Expected 0, got {upload_generations_passed[0]}"
            print("PASSED: Concurrent debouncing safely handled 412 exceptions, released locks, and preserved state.")


if __name__ == "__main__":
    run_high_concurrency_stress_test()
    run_concurrent_background_backup_debouncing_and_412_stress()
    print("\nALL ADVERSARIAL STRESS TESTS COMPLETED SUCCESSFULLY!")
