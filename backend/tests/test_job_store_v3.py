import pytest
import sqlite3
import os
import json
from pathlib import Path
from scripts.semantic_tagger.job_store import JobStore

@pytest.fixture
def store():
    db_path = "data/test_v3.sqlite3"
    if os.path.exists(db_path):
        os.remove(db_path)
    return JobStore(Path(db_path))

def test_claim_next_job_atomicity(store):
    run_id = store.create_run({"settings": {"num_predict": 1024, "request_timeout_seconds": 120}})
    store.queue_job("key1", run_id, "u1", "hash1")
    
    job = store.claim_next_job(run_id, "w1")
    assert job is not None
    assert "attempt_id" in job
    assert "lease_token" in job
    assert job["attempt_count"] == 1
    assert job["status"] == "running"
    
    # Verify tagging_attempt was created
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        attempt = conn.execute("SELECT * FROM tagging_attempt WHERE attempt_id = ?", (job["attempt_id"],)).fetchone()
        assert attempt is not None
        assert attempt["job_id"] == job["job_id"]
        assert attempt["attempt_no"] == 1
        assert attempt["worker_id"] == "w1"
        assert attempt["lease_token"] == job["lease_token"]
        assert attempt["num_predict"] == 1024
        assert attempt["request_timeout_seconds"] == 120

def test_attempt_history_is_append_only(store):
    run_id = store.create_run({})
    store.queue_job("key1", run_id, "u1", "hash1")
    
    # Claim job, then fail it
    job = store.claim_next_job(run_id, "w1")
    store.fail_job(job["job_id"], job["attempt_id"], job["lease_token"], "error", "some error")
    
    # Re-claim job
    job2 = store.claim_next_job(run_id, "w1")
    assert job2["attempt_count"] == 2
    
    with sqlite3.connect(store.db_path) as conn:
        attempts = conn.execute("SELECT * FROM tagging_attempt WHERE job_id = ? ORDER BY attempt_no", (job["job_id"],)).fetchall()
        assert len(attempts) == 2
        assert attempts[0][2] == 1 # attempt_no
        assert attempts[0][8] == "failed" # status
        assert attempts[1][2] == 2 # attempt_no
        assert attempts[1][8] == "running" # status

def test_renew_lease_with_token(store):
    run_id = store.create_run({})
    store.queue_job("key1", run_id, "u1", "hash1")
    job = store.claim_next_job(run_id, "w1")
    
    # Renew with correct token
    store.renew_lease("w1", job["lease_token"], 300)
    
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute("SELECT lease_expires_at FROM tagging_job WHERE job_id = ?", (job["job_id"],)).fetchone()
        expires1 = j["lease_expires_at"]
        
        # Renew with wrong token (should do nothing)
        store.renew_lease("w1", "wrong-token", 900)
        j2 = conn.execute("SELECT lease_expires_at FROM tagging_job WHERE job_id = ?", (job["job_id"],)).fetchone()
        assert j2["lease_expires_at"] == expires1

def test_complete_fail_with_token(store):
    run_id = store.create_run({})
    store.queue_job("key1", run_id, "u1", "hash1")
    job = store.claim_next_job(run_id, "w1")
    
    with pytest.raises(RuntimeError, match="invalid token"):
        store.complete_job(job["job_id"], job["attempt_id"], "wrong", "{}", "out", 100, 10, 10)
        
    with pytest.raises(RuntimeError, match="invalid token"):
        store.fail_job(job["job_id"], job["attempt_id"], "wrong", "err", "err")
        
    # Correct token works
    store.complete_job(job["job_id"], job["attempt_id"], job["lease_token"], "{}", "out", 100, 10, 10)
    
    with sqlite3.connect(store.db_path) as conn:
        st = conn.execute("SELECT status FROM tagging_job WHERE job_id = ?", (job["job_id"],)).fetchone()[0]
        assert st == "done"

def test_generation_config_hash_in_attempt(store):
    settings = {"think": True, "temperature": 0.5, "num_predict": 50}
    run_id = store.create_run({"settings": settings})
    store.queue_job("key1", run_id, "u1", "hash1")
    job = store.claim_next_job(run_id, "w1")
    
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        attempt = conn.execute("SELECT generation_config_hash FROM tagging_attempt WHERE attempt_id = ?", (job["attempt_id"],)).fetchone()
        
        import hashlib
        expected_hash = hashlib.sha256(json.dumps({
            "think": True,
            "temperature": 0.5,
            "seed": 42,
            "stream": False,
            "num_predict": 50,
            "request_timeout_seconds": 3600
        }, sort_keys=True).encode()).hexdigest()
        
        assert attempt["generation_config_hash"] == expected_hash

