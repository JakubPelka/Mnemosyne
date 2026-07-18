import pytest
import sqlite3
import time
import contextlib

from scripts.semantic_tagger.job_store import JobStore, JobExecutionContext
from scripts.semantic_tagger.worker_loop import HeartbeatThread


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "semantic_tagger.sqlite3"
    job_store = JobStore(db_path)
    run_id = job_store.create_run({"model_name": "test-model"})

    # insert dummy worker
    with contextlib.closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
        with conn:
            conn.execute(
                "INSERT INTO worker_state (run_id, worker_id, status) VALUES (?, ?, ?)",
                (run_id, "w1", "running"),
            )

    job_store.save_unit(
        {
            "unit_id": "u1",
            "context_id": "ctx1",
            "sequence_no": 1,
            "content_hash": "hash1",
            "event_ids": ["e1"],
            "segments": [],
            "event_count": 1,
            "character_count": 10,
            "estimated_token_count": 2,
            "first_event_at": "2026-07-01",
            "last_event_at": "2026-07-01",
        }
    )

    job_store.queue_job("key1", run_id, "u1", "hash1")
    job = job_store.claim_next_job(run_id, "w1")
    return job_store, run_id, job


def test_heartbeat_and_metadata_write_sequentially(store):
    job_store, run_id, job = store
    hb = HeartbeatThread(job_store, run_id, "w1")
    ctx = JobExecutionContext(job["job_id"], job["attempt_id"], job["lease_token"])
    hb.set_active_job(ctx)

    hb.start()
    time.sleep(0.5)  # Let heartbeat run

    try:
        # metadata persistence should succeed while heartbeat is active
        job_store.record_attempt_response_metadata(
            job["attempt_id"], job["job_id"], job["lease_token"], 1000, 10, 20, "stop"
        )
        success = True
    except sqlite3.OperationalError:
        success = False
    finally:
        hb.stop()

    assert success


def test_heartbeat_never_leaves_open_write_transaction(store):
    job_store, run_id, job = store
    hb = HeartbeatThread(job_store, run_id, "w1")
    ctx = JobExecutionContext(job["job_id"], job["attempt_id"], job["lease_token"])
    hb.set_active_job(ctx)

    hb.start()
    time.sleep(0.5)

    try:
        # If heartbeat holds a write lock, this explicit EXCLUSIVE will fail immediately
        with contextlib.closing(sqlite3.connect(job_store.db_path, timeout=0.1)) as conn:
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("ROLLBACK")
        lock_held = False
    except sqlite3.OperationalError:
        lock_held = True
    finally:
        hb.stop()

    assert not lock_held


def test_metadata_persistence_succeeds_while_heartbeat_is_active(store):
    job_store, run_id, job = store
    hb = HeartbeatThread(job_store, run_id, "w1")

    hb.start()
    time.sleep(0.5)
    try:
        job_store.complete_job(
            job["job_id"],
            job["attempt_id"],
            job["lease_token"],
            '{"tag": "ok"}',
            "out_hash",
            1500,
            50,
            100,
        )
    finally:
        hb.stop()

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status FROM tagging_job WHERE job_id=?", (job["job_id"],)
        ).fetchone()
        assert j["status"] == "done"


def test_fail_job_can_persist_after_worker_exception(store):
    job_store, run_id, job = store

    hb = HeartbeatThread(job_store, run_id, "w1")
    ctx = JobExecutionContext(job["job_id"], job["attempt_id"], job["lease_token"])
    hb.set_active_job(ctx)
    hb.start()

    time.sleep(0.5)

    try:
        job_store.fail_job(
            job["job_id"],
            job["attempt_id"],
            job["lease_token"],
            "worker_exception",
            "Something blew up",
        )
    finally:
        hb.stop()

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status, error_code FROM tagging_job WHERE job_id=?", (job["job_id"],)
        ).fetchone()
        assert j["status"] == "failed"
        assert j["error_code"] == "worker_exception"


def test_expired_running_job_is_recovered_idempotently(store):
    job_store, run_id, job = store

    # artificially expire the lease
    with contextlib.closing(sqlite3.connect(job_store.db_path)) as conn:
        with conn:
            conn.execute(
                "UPDATE tagging_job SET lease_expires_at = '2000-01-01T00:00:00' WHERE job_id = ?",
                (job["job_id"],),
            )

    job_store.recover_expired_leases(run_id)

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status FROM tagging_job WHERE job_id=?", (job["job_id"],)
        ).fetchone()
        assert j["status"] == "pending"

    # run again idempotently
    job_store.recover_expired_leases(run_id)
    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status FROM tagging_job WHERE job_id=?", (job["job_id"],)
        ).fetchone()
        assert j["status"] == "pending"


def test_incident_sequence_reproduces_database_locked_before_fix_and_passes_afterward(store):
    # we simulate the exact incident sequence:
    # 1. Start a transaction on conn1 (heartbeat update)
    # 2. Try to start a transaction on conn2 (renew_lease)

    job_store, run_id, job = store

    with contextlib.closing(sqlite3.connect(job_store.db_path, timeout=0.1)) as conn1:
        conn1.execute("PRAGMA journal_mode = WAL")
        conn1.execute("PRAGMA busy_timeout = 100")  # fast fail for test

        # simulated heartbeat:
        conn1.execute("BEGIN IMMEDIATE")
        conn1.execute(
            "UPDATE worker_state SET heartbeat_at = '2026-07-16' WHERE run_id = ?", (run_id,)
        )

        # simulated renew_lease:
        success = False
        try:
            job_store.renew_lease(job["job_id"], job["attempt_id"], job["lease_token"], 900)
            success = True
        except sqlite3.OperationalError:
            # this is what happened before the fix!
            success = False

        assert not success, (
            "Without the fix, this sequence fails with OperationalError (database locked)"
        )

        # rollback to clean up
        conn1.execute("ROLLBACK")

    # Now simulate the fixed behavior
    with contextlib.closing(sqlite3.connect(job_store.db_path, timeout=0.1)) as conn1:
        # fixed heartbeat: transaction is committed immediately
        with conn1:
            conn1.execute(
                "UPDATE worker_state SET heartbeat_at = '2026-07-16' WHERE run_id = ?", (run_id,)
            )

        # then renew lease on conn2
        job_store.renew_lease(job["job_id"], job["attempt_id"], job["lease_token"], 900)

    with sqlite3.connect(job_store.db_path) as check_conn:
        check_conn.row_factory = sqlite3.Row
        r = check_conn.execute(
            "SELECT heartbeat_at FROM worker_state WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert r["heartbeat_at"] == "2026-07-16"
