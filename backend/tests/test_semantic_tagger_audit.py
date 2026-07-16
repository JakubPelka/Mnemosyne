import pytest
import sqlite3
import json
import time
from pathlib import Path
from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit, MAIN_DB_URI
from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.worker_loop import run_worker_loop


@pytest.fixture
def test_db_paths(tmp_path):
    main_db = tmp_path / "main.sqlite3"
    sidecar_db = tmp_path / "sidecar.sqlite3"

    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT, context_id TEXT, title TEXT, text TEXT, timestamp_start TEXT, event_type TEXT)"
        )
        conn.commit()

    import scripts.semantic_tagger.content_loader

    scripts.semantic_tagger.content_loader.MAIN_DB_URI = f"file:{main_db}?mode=ro"

    return main_db, sidecar_db


def test_secret_title_absent_from_sidecar(test_db_paths):
    main_db, sidecar_db = test_db_paths

    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "INSERT INTO events VALUES ('e1', 'ctx1', 'SECRET_TITLE', 'Hello world.', '2026', 'user')"
        )
        conn.commit()

    builder = UnitBuilder()
    events = [
        {
            "event_id": "e1",
            "context_id": "ctx1",
            "text": "Hello world.",
            "timestamp_start": "2026",
            "event_type": "user",
        }
    ]
    units = builder.build_units_for_context("ctx1", events, title="SECRET_TITLE")

    store = JobStore(sidecar_db)
    u = units[0]
    store.save_unit(u)

    with sqlite3.connect(sidecar_db) as conn:
        conn.row_factory = sqlite3.Row
        saved = conn.execute(
            "SELECT segments_json FROM tagging_unit WHERE unit_id = ?", (u["unit_id"],)
        ).fetchone()
        assert "SECRET_TITLE" not in saved["segments_json"]


def test_prepare_and_reconstruct_hash_match_with_title(test_db_paths):
    main_db, sidecar_db = test_db_paths

    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "INSERT INTO events VALUES ('e1', 'ctx1', 'SECRET_TITLE', 'Hello world.', '2026', 'user')"
        )
        conn.commit()

    builder = UnitBuilder()
    events = [
        {
            "event_id": "e1",
            "context_id": "ctx1",
            "text": "Hello world.",
            "timestamp_start": "2026",
            "event_type": "user",
        }
    ]
    units = builder.build_units_for_context("ctx1", events, title="SECRET_TITLE")

    store = JobStore(sidecar_db)
    u = units[0]
    store.save_unit(u)

    reconstructed = load_and_reconstruct_unit(
        u["unit_id"], str(sidecar_db), builder.schema_version, builder.strategy_version
    )
    assert reconstructed.content_hash == u["content_hash"]


def test_content_signals_detect_code_logs_urls(test_db_paths):
    main_db, sidecar_db = test_db_paths
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "INSERT INTO events VALUES ('e1', 'ctx1', '', 'http://foo def bar(): ERROR', '2026', 'user')"
        )

    builder = UnitBuilder()
    events = [
        {
            "event_id": "e1",
            "context_id": "ctx1",
            "text": "http://foo def bar(): ERROR",
            "timestamp_start": "2026",
            "event_type": "user",
        }
    ]
    units = builder.build_units_for_context("ctx1", events, title="")
    store = JobStore(sidecar_db)
    store.save_unit(units[0])

    rec = load_and_reconstruct_unit(
        units[0]["unit_id"], str(sidecar_db), builder.schema_version, builder.strategy_version
    )
    assert rec.contains_code is True
    assert rec.contains_logs is True
    assert rec.contains_urls is True


def test_unit_builder_overlap_carry():
    builder = UnitBuilder(max_events=1, overlap_events=1)
    events = [
        {"event_id": "e1", "text": "1", "event_type": "user"},
        {"event_id": "e2", "text": "2", "event_type": "user"},
    ]
    units = builder.build_units_for_context("ctx1", events)
    assert len(units) == 2
    assert units[0]["event_ids"] == ["e1"]
    assert units[1]["event_ids"] == ["e1", "e2"]

    # Check is_overlap flag in manifest segments
    assert units[1]["segments"]["segments"][0]["is_overlap"] is True
    assert units[1]["segments"]["segments"][1]["is_overlap"] is False


def test_audit_retry_numbering(test_db_paths):
    main_db, sidecar_db = test_db_paths

    prompts_seen = []

    class MockClient:
        def generate_tags(self, p, s):
            prompts_seen.append(p)
            from scripts.semantic_tagger.ollama_client import OllamaError

            raise OllamaError("Fail")

    store = JobStore(sidecar_db)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_job (job_id, attempt_count, status) VALUES ('j1', 1, 'running')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, attempt_count, status) VALUES ('j2', 2, 'running')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, attempt_count, status) VALUES ('j3', 3, 'running')"
        )
        conn.commit()

    worker = Worker(store, MockClient())

    class MockUnit:
        contains_code = False
        contains_logs = False
        contains_urls = False
        content = "test"

    worker.run_one({"job_id": "j1", "attempt_count": 1}, MockUnit())
    assert "Ostatnia próba" not in prompts_seen[0]

    worker.run_one({"job_id": "j2", "attempt_count": 2}, MockUnit())
    assert "Zwróć tylko 100% poprawne dane" in prompts_seen[1]

    worker.run_one({"job_id": "j3", "attempt_count": 3}, MockUnit())
    assert "Zwróć maksymalnie 6 pojęć" in prompts_seen[2]


def test_max_jobs_and_isolation(test_db_paths):
    main_db, sidecar_db = test_db_paths

    store = JobStore(sidecar_db)
    run_info = {"model_name": "test_model"}
    run_id = store.create_run(run_info)

    # 5 syntetic jobs
    with sqlite3.connect(store.db_path) as conn:
        for i in range(5):
            conn.execute(
                "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES (?, 'ctx1', 'hash', '{}')",
                (f"u{i}",),
            )
            conn.execute(
                "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES (?, ?, ?, ?, 'pending')",
                (f"j{i}", f"k{i}", run_id, f"u{i}"),
            )

    import unittest.mock as mock

    with (
        mock.patch("scripts.semantic_tagger.worker.Worker.run_one", return_value=True),
        mock.patch(
            "scripts.semantic_tagger.worker_loop.load_and_reconstruct_unit",
            return_value=mock.MagicMock(),
        ),
    ):
        # max_jobs = 2
        run_worker_loop("test_model", target_run_id=run_id, max_jobs=2, store=store)

        # Check jobs
        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Should have processed 2 jobs
            processed = conn.execute(
                "SELECT * FROM tagging_job WHERE status != 'pending'"
            ).fetchall()
            assert len(processed) == 2

            pending = conn.execute("SELECT * FROM tagging_job WHERE status = 'pending'").fetchall()
            assert len(pending) == 3


def test_worker_lock(test_db_paths):
    main_db, sidecar_db = test_db_paths
    store = JobStore(sidecar_db)
    run_id = store.create_run({"model_name": "test_model"})

    # Setup live worker state
    with sqlite3.connect(store.db_path) as conn:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO worker_state (run_id, worker_id, status, heartbeat_at, pause_requested, stop_after_current_requested) VALUES (?, 'w1', 'running', ?, 0, 0)",
            (run_id, now),
        )

    # Another worker should abort
    import sys, io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        run_worker_loop("test_model", target_run_id=run_id, max_jobs=1, store=store)
    finally:
        out = sys.stdout.getvalue()
        sys.stdout = old_stdout

    assert "Another live worker already owns this run." in out
