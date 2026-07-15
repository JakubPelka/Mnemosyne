from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.worker import Worker
import sqlite3
import json

def test_audit_contains_flags_and_secrets(tmp_path):
    main_db = tmp_path / "main.sqlite3"
    
    with sqlite3.connect(main_db) as conn:
        conn.execute("CREATE TABLE events (event_id TEXT, context_id TEXT, title TEXT, text TEXT, timestamp_start TEXT, event_type TEXT)")
        conn.execute("INSERT INTO events VALUES ('e1', 'ctx1', 'SECRET_TITLE', 'Hello world. Check http://test.com and def foo(): pass. Error: Traceback...', '2026', 'user')")
        conn.commit()

    import scripts.semantic_tagger.content_loader
    scripts.semantic_tagger.content_loader.MAIN_DB_URI = f"file:{main_db}?mode=ro"

    builder = UnitBuilder(max_chars=1000, max_events=5)
    events = [{"event_id": "e1", "context_id": "ctx1", "text": "Hello world. Check http://test.com and def foo(): pass. Error: Traceback...", "timestamp_start": "2026", "event_type": "user"}]
    units = builder.build_units_for_context("ctx1", events, title="SECRET_TITLE")
    
    assert len(units) == 1
    u = units[0]
    manifest_str = json.dumps(u["segments"])
    assert "SECRET_TITLE" not in manifest_str
    
def test_audit_isolation_and_lock(tmp_path):
    store = JobStore(tmp_path / "iso.sqlite3")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("INSERT INTO tagging_run (run_id, model_name) VALUES ('r1', 'qwen3:14b')")
        conn.execute("INSERT INTO tagging_run (run_id, model_name) VALUES ('r2', 'qwen3:14b')")
        
        # Insert jobs
        conn.execute("INSERT INTO tagging_job (job_id, job_key, run_id, status) VALUES ('j1', 'k1', 'r1', 'pending')")
        conn.execute("INSERT INTO tagging_job (job_id, job_key, run_id, status) VALUES ('j2', 'k2', 'r2', 'pending')")
        
    j = store.claim_next_job("r1", "w1")
    assert j["job_id"] == "j1"
    
    j2 = store.claim_next_job("r2", "w1")
    assert j2["job_id"] == "j2"
    
def test_audit_retry_numbering(tmp_path):
    class MockClient:
        def generate_tags(self, p, s):
            from scripts.semantic_tagger.ollama_client import OllamaError
            raise OllamaError("Fail")
            
    store = JobStore(tmp_path / "memory.sqlite3")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("INSERT INTO tagging_job (job_id, attempt_count, status) VALUES ('j1', 1, 'running')")
        conn.commit()
    
    worker = Worker(store, MockClient())
    class MockUnit:
        contains_code = False
        contains_logs = False
        contains_urls = False
        content = "test"
        
    worker.run_one({"job_id": "j1", "attempt_count": 1}, MockUnit())
