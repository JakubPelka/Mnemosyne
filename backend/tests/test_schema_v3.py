import sqlite3
from scripts.semantic_tagger.job_store import JobStore


def test_schema_v3(tmp_path):
    store = JobStore(tmp_path / "test_schema.sqlite3")

    assert store.EXPECTED_SCHEMA_VERSION == 3

    with sqlite3.connect(store.db_path) as conn:
        meta = conn.execute(
            "SELECT value FROM sidecar_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert meta[0] == "3"

        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t[0] for t in tables]
        assert "tagging_job" in table_names
        assert "tagging_attempt" in table_names
