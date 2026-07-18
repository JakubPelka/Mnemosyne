import sqlite3
import pytest
from scripts.semantic_tagger.job_store import JobStore


def test_schema_v4_and_columns(tmp_path):
    store = JobStore(tmp_path / "test_schema.sqlite3")

    assert store.SIDECAR_DB_SCHEMA_VERSION == 4

    with sqlite3.connect(store.db_path) as conn:
        meta = conn.execute(
            "SELECT value FROM sidecar_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert meta[0] == "4"

        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t[0] for t in tables]
        assert "tagging_job" in table_names
        assert "tagging_attempt" in table_names

        columns = conn.execute("PRAGMA table_info(tagging_attempt)").fetchall()
        column_names = [c[1] for c in columns]
        assert "response_output_text" in column_names
        assert "response_output_hash" in column_names
        assert "response_output_truncated" in column_names
        assert "response_output_bytes" in column_names
        assert "response_output_stored_bytes" in column_names


def test_rejects_v3_sidecar_without_mutation(tmp_path):
    db_path = tmp_path / "legacy_v3.sqlite3"

    # Create a dummy v3 sidecar manually
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sidecar_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sidecar_meta (key, value) VALUES ('schema_version', '3')")
        # create a minimal attempt table without the new columns
        conn.execute("CREATE TABLE tagging_attempt (attempt_id TEXT PRIMARY KEY)")
        conn.commit()

    # Capture original hash
    import hashlib

    original_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    # Attempt to open it with JobStore
    with pytest.raises(RuntimeError) as excinfo:
        JobStore(db_path)

    assert "Sidecar schema mismatch" in str(excinfo.value)
    assert "Expected: 4" in str(excinfo.value)
    assert "Found: 3" in str(excinfo.value)

    # Assert sidecar is unchanged
    new_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert new_hash == original_hash
