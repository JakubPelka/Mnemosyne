from scripts.semantic_tagger.ollama_client import OllamaClient
from scripts.semantic_tagger.cli import cmd_status
from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.worker import Worker
import pytest
import sqlite3
import json
import unittest.mock as mock
import argparse
import time
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit
from scripts.semantic_tagger.worker_loop import run_worker_loop


@pytest.fixture
def test_db_paths(tmp_path):
    main_db = tmp_path / "main.sqlite3"
    sidecar_db = tmp_path / "sidecar.sqlite3"

    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT, context_id TEXT, title TEXT, text TEXT, timestamp_start TEXT, event_type TEXT)"
        )
        conn.execute("CREATE TABLE chatgpt_messages (event_id TEXT, role TEXT)")
        conn.commit()

    import scripts.semantic_tagger.content_loader

    scripts.semantic_tagger.content_loader.MAIN_DB_URI = f"file:{main_db}?mode=ro"

    return main_db, sidecar_db


def create_worker_runtime_paths(tmp_path):
    main_db = tmp_path / "worker-main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events (event_id TEXT, context_id TEXT, title TEXT, text TEXT, "
            "timestamp_start TEXT, event_type TEXT)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS chatgpt_messages (event_id TEXT, role TEXT)")
    return {
        "main_db_path": main_db,
        "vocabulary_db_path": tmp_path / "worker-vocabulary.sqlite3",
    }


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
    builder = UnitBuilder(max_events=2, overlap_events=1)
    events = [
        {"event_id": "e1", "text": "1", "event_type": "user"},
        {"event_id": "e2", "text": "2", "event_type": "user"},
        {"event_id": "e3", "text": "3", "event_type": "user"},
    ]
    units = builder.build_units_for_context("ctx1", events)
    assert len(units) == 2
    assert units[0]["event_ids"] == ["e1", "e2"]
    assert units[1]["event_ids"] == ["e2", "e3"]

    # Check is_overlap flag in manifest segments
    assert units[1]["segments"]["segments"][0]["is_overlap"] is True
    assert units[1]["segments"]["segments"][1]["is_overlap"] is False


def test_audit_retry_numbering(test_db_paths):
    main_db, sidecar_db = test_db_paths

    prompts_seen = []

    class MockClient:
        def generate_tags(self, p, s, num_predict=4096, seed=42, num_ctx=8192):
            prompts_seen.append(p)
            from scripts.semantic_tagger.ollama_client import OllamaError

            raise OllamaError("Fail")

    store = JobStore(sidecar_db)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_job (job_id, attempt_count, status, lease_token) VALUES ('j1', 1, 'running', 'l1')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, attempt_count, status, lease_token) VALUES ('j2', 2, 'running', 'l2')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, attempt_count, status, lease_token) VALUES ('j3', 3, 'running', 'l3')"
        )
        conn.commit()

    worker = Worker(
        store,
        MockClient(),
        vocabulary_db_path=sidecar_db.with_name("worker-vocabulary.sqlite3"),
    )

    class MockUnit:
        contains_code = False
        contains_logs = False
        contains_urls = False
        content = "test"
        event_ids = []

    worker.run_one(
        {
            "job_id": "j1",
            "attempt_count": 1,
            "attempt_id": "a1",
            "lease_token": "l1",
            "prompt_version": "semantic-hybrid-v1",
            "schema_version": "semantic-tags-v1",
            "settings": {"num_predict": 1024, "seed": 42, "num_ctx": 4096},
            "num_predict": 1024,
            "seed": 42,
            "num_ctx": 4096,
        },
        MockUnit(),
    )
    assert "Ostatnia próba" not in prompts_seen[0]

    worker.run_one(
        {
            "job_id": "j2",
            "attempt_count": 2,
            "attempt_id": "a2",
            "lease_token": "l2",
            "prompt_version": "semantic-hybrid-v1",
            "schema_version": "semantic-tags-v1",
            "settings": {"num_predict": 1024, "seed": 42, "num_ctx": 4096},
            "num_predict": 1024,
            "seed": 42,
            "num_ctx": 4096,
        },
        MockUnit(),
    )
    assert "Zwróć tylko 100% poprawne dane" in prompts_seen[1]

    worker.run_one(
        {
            "job_id": "j3",
            "attempt_count": 3,
            "attempt_id": "a3",
            "lease_token": "l3",
            "prompt_version": "semantic-hybrid-v1",
            "schema_version": "semantic-tags-v1",
            "settings": {"num_predict": 1024, "seed": 42, "num_ctx": 4096},
            "num_predict": 1024,
            "seed": 42,
            "num_ctx": 4096,
        },
        MockUnit(),
    )
    assert "Zwróć maksymalnie 6 pojęć" in prompts_seen[2]


def test_max_claims_and_isolation(test_db_paths):
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
        # max_claims = 2
        run_worker_loop(
            "test_model",
            target_run_id=run_id,
            max_claims=2,
            store=store,
            main_db_path=main_db,
            vocabulary_db_path=sidecar_db.parent / "worker-vocabulary.sqlite3",
        )

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
    import sys
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        run_worker_loop(
            "test_model",
            target_run_id=run_id,
            max_claims=1,
            store=store,
            main_db_path=main_db,
            vocabulary_db_path=sidecar_db.parent / "worker-vocabulary.sqlite3",
        )
    finally:
        out = sys.stdout.getvalue()
        sys.stdout = old_stdout

    assert "Another live worker already owns this run." in out


def test_unit_builder_progress_when_max_events_equals_overlap():
    builder = UnitBuilder(max_events=1, overlap_events=1)
    events = [
        {"event_id": "e1", "text": "A"},
        {"event_id": "e2", "text": "B"},
        {"event_id": "e3", "text": "C"},
    ]
    units = builder.build_units_for_context("ctx", events)

    assert len(units) == 3
    assert units[0]["event_ids"] == ["e1"]
    assert units[1]["event_ids"] == ["e2"]
    assert units[2]["event_ids"] == ["e3"]


def test_unit_builder_rejects_zero_max_events():
    with pytest.raises(ValueError, match="max_events must be at least 1"):
        UnitBuilder(max_events=0, overlap_events=1)
    with pytest.raises(ValueError, match="max_events must be at least 1"):
        UnitBuilder(max_events=-1)
    with pytest.raises(ValueError, match="overlap_events must be at least 0"):
        UnitBuilder(max_events=10, overlap_events=-1)


def test_max_claims_counts_claims_not_loop_iterations(tmp_path):
    # max_claims applies to successfully claimed jobs
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": {},
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u2', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'pending')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j2', 'k2', ?, 'u2', 'pending')",
            (run_id,),
        )

    with (
        mock.patch("scripts.semantic_tagger.worker_loop.Worker") as MockWorker,
        mock.patch("scripts.semantic_tagger.worker_loop.load_and_reconstruct_unit") as _,
        mock.patch("time.sleep") as mock_sleep,
    ):
        worker_instance = mock.MagicMock()
        worker_instance.run_one.return_value = True
        MockWorker.return_value = worker_instance

        # We need a custom claim_next_job that returns None once, then claims, to prove loop iterations without claims don't count
        orig_claim = store.claim_next_job
        claim_calls = 0

        def fake_claim(*args):
            nonlocal claim_calls
            claim_calls += 1
            if claim_calls == 1:
                return None
            return orig_claim(*args)

        with mock.patch.object(store, "claim_next_job", side_effect=fake_claim):
            run_worker_loop(
                "qwen3:14b",
                target_run_id=run_id,
                max_claims=2,
                store=store,
                **create_worker_runtime_paths(tmp_path),
            )

        assert worker_instance.run_one.call_count == 2
        assert mock_sleep.call_count >= 1


def test_target_done_counts_successes_for_selected_run(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": {},
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'done')",
            (run_id,),
        )

    with (
        mock.patch("scripts.semantic_tagger.worker_loop.Worker") as MockWorker,
        mock.patch("scripts.semantic_tagger.worker_loop.load_and_reconstruct_unit"),
    ):
        MockWorker.return_value.run_one.return_value = True
        run_worker_loop(
            "qwen3:14b",
            target_run_id=run_id,
            target_done=1,
            store=store,
            **create_worker_runtime_paths(tmp_path),
        )
        assert MockWorker.return_value.run_one.call_count == 0


def test_failed_job_requires_explicit_retry(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": {},
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'pending')",
            (run_id,),
        )

    with (
        mock.patch("scripts.semantic_tagger.worker_loop.Worker") as MockWorker,
        mock.patch("scripts.semantic_tagger.worker_loop.load_and_reconstruct_unit"),
    ):
        worker_inst = mock.MagicMock()

        def fake_run_one(job, unit):
            store.fail_job(job["job_id"], job["attempt_id"], job["lease_token"], "sys", "sys")
            return False

        worker_inst.run_one.side_effect = fake_run_one

        MockWorker.return_value = worker_inst

        run_worker_loop(
            "qwen3:14b",
            target_run_id=run_id,
            max_claims=1,
            store=store,
            **create_worker_runtime_paths(tmp_path),
        )

    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT status FROM tagging_job WHERE job_id = 'j1'").fetchone()
        assert row[0] == "failed"

    # Run again, max_claims=1. It should NOT pick up the failed job.
    with (
        mock.patch("scripts.semantic_tagger.worker_loop.Worker") as MockWorker2,
        mock.patch("scripts.semantic_tagger.worker_loop.load_and_reconstruct_unit"),
        mock.patch("time.sleep", side_effect=InterruptedError),
    ):
        # We raise InterruptedError on sleep to break the loop since it would just poll forever
        try:
            run_worker_loop(
                "qwen3:14b",
                target_run_id=run_id,
                max_claims=1,
                store=store,
                **create_worker_runtime_paths(tmp_path),
            )
        except InterruptedError:
            pass
        assert MockWorker2.return_value.run_one.call_count == 0


def test_no_job_available_does_not_consume_claim(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": {},
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with (
        mock.patch("scripts.semantic_tagger.worker_loop.Worker") as MockWorker,
        mock.patch("scripts.semantic_tagger.worker_loop.load_and_reconstruct_unit"),
        mock.patch("time.sleep") as mock_sleep,
    ):
        mock_sleep.side_effect = InterruptedError
        try:
            run_worker_loop(
                "qwen3:14b",
                target_run_id=run_id,
                max_claims=1,
                store=store,
                **create_worker_runtime_paths(tmp_path),
            )
        except InterruptedError:
            pass
        assert MockWorker.return_value.run_one.call_count == 0


def test_stale_worker_cannot_complete_after_takeover(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": {},
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'pending')",
            (run_id,),
        )

    # Claim job with expired lease
    job = store.claim_next_job(run_id, "worker1", lease_seconds=-10)

    # Recover the job via periodic cleanup logic inside claim_next_job (which should reclaim expired ones)
    store.recover_expired_leases(run_id)
    job2 = store.claim_next_job(run_id, "worker2")
    assert job2["attempt_id"] != job["attempt_id"]

    # Stale worker tries to complete
    with pytest.raises(RuntimeError, match="lease expired or invalid token"):
        store.complete_job("j1", job["attempt_id"], job["lease_token"], "{}", "hash", 100, 10, 10)


def test_heartbeat_uses_configured_sidecar(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": {},
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO worker_state (run_id, worker_id, status) VALUES (?, 'w1', 'running')",
            (run_id,),
        )

    from scripts.semantic_tagger.worker_loop import HeartbeatThread

    heartbeat = HeartbeatThread(store, run_id, "w1")

    # Run a single loop using mock
    with mock.patch.object(heartbeat._stop_event, "wait", side_effect=InterruptedError):
        try:
            heartbeat.run()
        except InterruptedError:
            pass

    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT heartbeat_at FROM worker_state WHERE worker_id = 'w1'"
        ).fetchone()
        assert row[0] is not None


def test_claimed_job_contains_generation_settings(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    settings = {
        "think": False,
        "stream": False,
        "temperature": 0.0,
        "seed": 42,
        "num_predict": 2048,
        "num_ctx": 8192,
        "request_timeout_seconds": 3600,
    }
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": settings,
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'pending')",
            (run_id,),
        )

    job = store.claim_next_job(run_id, "w1")
    assert job["num_predict"] == 2048
    assert job["seed"] == 42


def test_worker_passes_generation_settings_to_client(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    client = mock.MagicMock()
    # fake return valid_output, prompt_tokens, completion_tokens, done_reason
    mock_out = mock.MagicMock()
    mock_out.model_dump_json.return_value = "{}"
    from scripts.semantic_tagger.ollama_client import OllamaGenerationResult

    client.generate_tags.return_value = OllamaGenerationResult("{}", 10, 10, 100, "stop")
    worker = Worker(
        store,
        client,
        vocabulary_db_path=tmp_path / "worker-vocabulary.sqlite3",
    )
    store.complete_job = mock.MagicMock()
    store.fail_job = mock.MagicMock()
    store.record_attempt_response_metadata = mock.MagicMock()

    job = {
        "job_id": "j1",
        "attempt_count": 1,
        "attempt_id": "a1",
        "lease_token": "tok",
        "prompt_version": "semantic-hybrid-v1",
        "schema_version": "semantic-tags-v1",
        "settings": {"num_predict": 1024, "seed": 99, "num_ctx": 8192},
        "num_predict": 1024,
        "seed": 99,
        "num_ctx": 8192,
    }
    unit = mock.MagicMock()
    unit.render_prompt.return_value = "prompt"

    worker.run_one(job, unit)

    client.generate_tags.assert_called_once()
    args, kwargs = client.generate_tags.call_args
    assert kwargs["num_predict"] == 1024  # num_predict
    assert kwargs["seed"] == 99  # seed
    assert kwargs["num_ctx"] == 8192


def test_ollama_payload_contains_num_predict_and_seed():
    client = OllamaClient("qwen3:14b")

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps({"tags": [], "done_reason": "stop"}).encode(
            "utf-8"
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        try:
            client.generate_tags("prompt", {}, num_predict=1024, seed=99)
        except Exception:
            pass

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["options"]["num_predict"] == 1024
        assert payload["options"]["seed"] == 99


def test_generation_hash_changes_with_num_predict(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()

    s1 = {"num_predict": 1024}
    r1 = store.create_run(
        {"model_name": "m", "settings": s1, "schema_version": "v1", "unit_strategy_version": "v1"}
    )

    s2 = {"num_predict": 2048}
    r2 = store.create_run(
        {"model_name": "m", "settings": s2, "schema_version": "v1", "unit_strategy_version": "v1"}
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'pending')",
            (r1,),
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j2', 'k2', ?, 'u1', 'pending')",
            (r2,),
        )

    job1 = store.claim_next_job(r1, "w1")
    job2 = store.claim_next_job(r2, "w2")

    with sqlite3.connect(store.db_path) as conn:
        hash1 = conn.execute(
            "SELECT generation_config_hash FROM tagging_attempt WHERE attempt_id = ?",
            (job1["attempt_id"],),
        ).fetchone()[0]
        hash2 = conn.execute(
            "SELECT generation_config_hash FROM tagging_attempt WHERE attempt_id = ?",
            (job2["attempt_id"],),
        ).fetchone()[0]
        assert hash1 != hash2


def test_truncated_response_is_failed_as_output_truncated(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings": {},
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'pending')",
            (run_id,),
        )

    job = store.claim_next_job(run_id, "w1")

    client = mock.MagicMock()
    # return done_reason="length"
    from scripts.semantic_tagger.ollama_client import OllamaGenerationResult

    client.generate_tags.return_value = OllamaGenerationResult("{}", 10, 4096, 100, "length")
    worker = Worker(
        store,
        client,
        vocabulary_db_path=tmp_path / "worker-vocabulary.sqlite3",
    )
    store.complete_job = mock.MagicMock()

    store.record_attempt_response_metadata = mock.MagicMock()

    unit = mock.MagicMock()
    unit.render_prompt.return_value = "prompt"

    success = worker.run_one(job, unit)
    assert not success

    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT status, error_code, done_reason FROM tagging_attempt WHERE attempt_id = ?",
            (job["attempt_id"],),
        ).fetchone()
        assert row[0] == "failed"
        assert row[1] == "output_truncated"
        assert row[2] == "length"


def test_report_computes_json_rate_with_mixed_outcomes(tmp_path, capsys):
    from scripts.semantic_tagger.job_store import JobStore

    store = JobStore(tmp_path / "test.sqlite3")
    store._init_db()
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "settings_json": "{}",
            "schema_version": "v1",
            "unit_strategy_version": "v1",
            "prompt_version": "semantic-hybrid-v1",
        }
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u1', 'c1', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u2', 'c2', 'hash', '{}')"
        )
        conn.execute(
            "INSERT INTO tagging_unit (unit_id, context_id, content_hash, segments_json) VALUES ('u3', 'c3', 'hash', '{}')"
        )

        # 3 jobs. 1 done, 1 failed (json), 1 failed (other)
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status) VALUES ('j1', 'k1', ?, 'u1', 'done')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status, error_code) VALUES ('j2', 'k2', ?, 'u2', 'failed', 'ollama_error')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status, error_code) VALUES ('j3', 'k3', ?, 'u3', 'failed', 'system_error')",
            (run_id,),
        )

    args = argparse.Namespace(watch=0, json=False)
    with mock.patch("scripts.semantic_tagger.cli.get_active_run_id", return_value=(run_id, store)):
        cmd_status(args)

    captured = capsys.readouterr()
    assert "valid JSON:" in captured.out
