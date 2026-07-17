from scripts.semantic_tagger.job_store import JobStore


def test_job_resume_and_skip(tmp_path):

    store = JobStore(tmp_path / "test_resume.sqlite3")

    # Insert run and unit
    run_id = store.create_run({})

    unit1 = "u1"
    unit2 = "u2"
    unit3 = "u3"

    # Queue 3 jobs
    store.queue_job("key1", run_id, unit1, "hash1")
    store.queue_job("key2", run_id, unit2, "hash2")
    store.queue_job("key3", run_id, unit3, "hash3")

    # Initial state: 3 pending
    pending = store.get_pending_jobs(run_id, 10)
    assert len(pending) == 3

    # 1. Job 1 is done
    job1 = next(j for j in pending if j["job_key"] == "key1")
    claimed = store.claim_next_job(run_id, "w1")  # To transition to running state
    store.complete_job(
        job1["job_id"], claimed["attempt_id"], claimed["lease_token"], "{}", "out_hash", 100, 10, 10
    )

    # 2. Job 2 is interrupted (running, but expired lease)
    next(j for j in pending if j["job_key"] == "key2")
    store.claim_next_job(run_id, "w1", lease_seconds=-10)  # To transition to running state

    # 3. Job 3 remains pending untouched

    # NOW: Restart worker (fetching pending jobs again)
    store.recover_expired_leases(run_id)
    new_pending = store.get_pending_jobs(run_id, 10)

    # Done jobs should be skipped
    keys = [j["job_key"] for j in new_pending]
    assert "key1" not in keys, "Done job should be skipped"

    # Expired job should be resumed
    assert "key2" in keys, "Expired running job should be resumed"

    # Untouched job remains
    assert "key3" in keys, "Untouched job should remain"

    # Verify the specific job IDs

    import sqlite3

    with sqlite3.connect(store.db_path) as conn:
        attempts = conn.execute(
            "SELECT * FROM tagging_attempt WHERE status = 'interrupted'"
        ).fetchall()
        # The history of the expired attempt should still exist!
        assert len(attempts) > 0, "History of expired attempts must remain in tagging_attempt"

    assert len(new_pending) == 2
