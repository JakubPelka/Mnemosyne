import json
import os
import signal
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

from scripts.semantic_tagger import cli
from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.ollama_client import OllamaGenerationResult
from scripts.semantic_tagger.runtime_paths import (
    CANONICAL_VOCABULARY_PATH,
    WorkerRuntimePathError,
    validate_worker_sidecar_path,
    validate_worker_runtime_paths,
)
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.worker_loop import mark_worker_inactive, run_worker_loop


VALID_OUTPUT = json.dumps(
    {
        "schema_version": "semantic-tags-v2",
        "languages": ["en"],
        "content_types": ["documentation"],
        "unit_quality": {
            "mostly_code": False,
            "mostly_logs": False,
            "insufficient_context": False,
        },
        "concepts": [
            {
                "concept_id": "C1",
                "surface_label": "runtime contract",
                "preferred_label": "runtime contract",
                "language": "en",
                "entity_types": [{"label": "process", "scheme": "local", "confidence": 0.9}],
                "domains": [{"label": "software", "scheme": "local", "confidence": 0.9}],
                "context_roles": [{"label": "subject", "scheme": "local", "confidence": 0.8}],
                "external_matches": [],
                "importance": 0.9,
                "confidence": 0.9,
                "evidence_event_ids": ["event-1"],
            }
        ],
        "relations": [],
    }
)
VALID_OUTPUT_V3 = json.dumps(
    {
        "schema_version": "semantic-tags-v3",
        "languages": ["en"],
        "content_types": ["documentation"],
        "unit_quality": "meaningful",
        "concepts": [
            {
                "concept_id": "C1",
                "surface_label": "runtime contract",
                "preferred_label": "runtime contract",
                "language": "en",
                "entity_types": ["process"],
                "domains": ["software"],
                "context_roles": ["subject"],
                "importance": 0.9,
                "confidence": 0.9,
                "evidence": ["E1"],
            }
        ],
        "relations": [],
    }
)


class FakeClient:
    output_text = VALID_OUTPUT
    constructed = 0
    calls = 0

    def __init__(self, _model):
        type(self).constructed += 1

    def generate_tags(self, _prompt, _schema_json, **_options):
        type(self).calls += 1
        return OllamaGenerationResult(self.output_text, 100, 40, 10, "stop")


class FailingClient(FakeClient):
    output_text = "not-json"


class FakeV3Client(FakeClient):
    output_text = VALID_OUTPUT_V3


def _create_main_db(path: Path, *, include_required_tables: bool = True) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, title TEXT, "
            "text TEXT, timestamp_start TEXT, event_type TEXT)"
        )
        if include_required_tables:
            conn.execute("CREATE TABLE chatgpt_messages (event_id TEXT, role TEXT)")
        conn.execute(
            "INSERT INTO events VALUES "
            "('event-1', 'context-1', '', 'Synthetic runtime contract input.', "
            "'2026-01-01T00:00:00', 'user')"
        )
    return path


def _create_sidecar(
    path: Path,
    *,
    jobs: int = 1,
    model: str = "test-model",
    schema_version: str = "semantic-tags-v2",
    prompt_version: str = "semantic-hybrid-v2",
):
    store = JobStore(path)
    run_id = store.create_run(
        {
            "model_name": model,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "unit_strategy_version": "unit-v2-whole-events",
            "settings": {"num_predict": 256, "seed": 42, "num_ctx": 2048},
        }
    )
    for index in range(jobs):
        event_id = "event-1"
        unit = UnitBuilder(overlap_events=0, schema_version=schema_version).build_units_for_context(
            "context-1",
            [
                {
                    "event_id": event_id,
                    "context_id": "context-1",
                    "text": "Synthetic runtime contract input.",
                    "timestamp_start": "2026-01-01T00:00:00",
                    "event_type": "user",
                }
            ],
        )[0]
        unit["unit_id"] = f"{unit['unit_id']}-{index}"
        store.save_unit(unit)
        store.queue_job(f"job-key-{index}", run_id, unit["unit_id"], unit["content_hash"])
    return store, run_id


def _counts(store: JobStore, run_id: str) -> dict[str, int]:
    with sqlite3.connect(store.db_path) as conn:
        result = {
            status: conn.execute(
                "SELECT count(*) FROM tagging_job WHERE run_id = ? AND status = ?",
                (run_id, status),
            ).fetchone()[0]
            for status in ("pending", "running", "done", "failed")
        }
        result["attempts"] = conn.execute(
            "SELECT count(*) FROM tagging_attempt a JOIN tagging_job j USING (job_id) "
            "WHERE j.run_id = ?",
            (run_id,),
        ).fetchone()[0]
        result["running_workers"] = conn.execute(
            "SELECT count(*) FROM worker_state WHERE run_id = ? AND status = 'running'",
            (run_id,),
        ).fetchone()[0]
    return result


def _worker_rows(store: JobStore, run_id: str) -> int:
    with sqlite3.connect(store.db_path) as conn:
        return conn.execute(
            "SELECT count(*) FROM worker_state WHERE run_id = ?", (run_id,)
        ).fetchone()[0]


def _run_paths(tmp_path: Path) -> tuple[Path, Path]:
    return _create_main_db(tmp_path / "main.sqlite3"), tmp_path / "vocabulary.sqlite3"


def _run_cli_preflight_failure(
    tmp_path: Path,
    monkeypatch,
    sidecar_path: Path,
    *,
    run_id: str = "missing-run",
    expected: str,
) -> Path:
    main_db = _create_main_db(tmp_path / "main.sqlite3")
    vocabulary_db = tmp_path / "isolated" / "vocabulary.sqlite3"
    FakeV3Client.constructed = 0
    monkeypatch.setattr("scripts.semantic_tagger.worker_loop.OllamaClient", FakeV3Client)

    with pytest.raises(SystemExit, match=expected):
        _call_cli(
            [
                "worker",
                "--run-id",
                run_id,
                "--db-path",
                str(sidecar_path),
                "--main-db",
                str(main_db),
                "--vocabulary-db",
                str(vocabulary_db),
                "--target-terminal",
                "1",
            ]
        )

    assert FakeV3Client.constructed == 0
    assert not vocabulary_db.exists()
    return vocabulary_db


def _assert_sidecar_has_no_runtime_mutation(path: Path, run_id: str) -> None:
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM worker_state").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM tagging_attempt").fetchone()[0] == 0
        counts = dict(
            conn.execute(
                "SELECT status, count(*) FROM tagging_job WHERE run_id = ? GROUP BY status",
                (run_id,),
            )
        )
    assert counts == {"pending": 1}


def test_cli_missing_sidecar_fails_without_creating_it(tmp_path, monkeypatch):
    missing = tmp_path / "missing-sidecar.sqlite3"
    _run_cli_preflight_failure(
        tmp_path,
        monkeypatch,
        missing,
        expected="--db-path: file does not exist",
    )
    assert not missing.exists()


def test_cli_malformed_sidecar_fails_before_runtime_mutation(tmp_path, monkeypatch):
    malformed = tmp_path / "malformed.sqlite3"
    malformed.write_bytes(b"not a SQLite database")
    original = malformed.read_bytes()
    _run_cli_preflight_failure(
        tmp_path,
        monkeypatch,
        malformed,
        expected="--db-path: SQLite validation failed",
    )
    assert malformed.read_bytes() == original


def test_cli_sidecar_without_worker_tables_fails_before_runtime_mutation(tmp_path, monkeypatch):
    incomplete = tmp_path / "incomplete.sqlite3"
    with sqlite3.connect(incomplete) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
    _run_cli_preflight_failure(
        tmp_path,
        monkeypatch,
        incomplete,
        expected="--db-path: required worker tables are missing",
    )
    with sqlite3.connect(incomplete) as conn:
        assert (
            conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
            == 1
        )


def test_cli_read_only_sidecar_fails_before_runtime_mutation(tmp_path, monkeypatch):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3")
    store.db_path.chmod(0o444)
    try:
        _run_cli_preflight_failure(
            tmp_path,
            monkeypatch,
            store.db_path,
            run_id=run_id,
            expected="--db-path: file is not writable",
        )
    finally:
        store.db_path.chmod(0o600)
    _assert_sidecar_has_no_runtime_mutation(store.db_path, run_id)


@pytest.mark.parametrize("symlink_target", ["file", "parent"])
def test_cli_sidecar_rejects_symlink_components(tmp_path, monkeypatch, symlink_target):
    actual_dir = tmp_path / "actual"
    actual_dir.mkdir()
    store, run_id = _create_sidecar(actual_dir / "sidecar.sqlite3")
    if symlink_target == "file":
        supplied = tmp_path / "sidecar-link.sqlite3"
        supplied.symlink_to(store.db_path)
    else:
        linked_parent = tmp_path / "sidecar-parent-link"
        linked_parent.symlink_to(actual_dir, target_is_directory=True)
        supplied = linked_parent / "sidecar.sqlite3"
    _run_cli_preflight_failure(
        tmp_path,
        monkeypatch,
        supplied,
        run_id=run_id,
        expected="--db-path: symbolic links are not accepted",
    )
    _assert_sidecar_has_no_runtime_mutation(store.db_path, run_id)


def test_cli_sidecar_rejects_hard_links(tmp_path, monkeypatch):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3")
    alias = tmp_path / "sidecar-hard-link.sqlite3"
    os.link(store.db_path, alias)
    _run_cli_preflight_failure(
        tmp_path,
        monkeypatch,
        alias,
        run_id=run_id,
        expected="--db-path: hard links are not accepted",
    )
    _assert_sidecar_has_no_runtime_mutation(store.db_path, run_id)


def test_cli_missing_requested_run_fails_before_runtime_mutation(tmp_path, monkeypatch):
    store, existing_run_id = _create_sidecar(tmp_path / "sidecar.sqlite3")
    _run_cli_preflight_failure(
        tmp_path,
        monkeypatch,
        store.db_path,
        expected="--db-path: requested run does not exist",
    )
    _assert_sidecar_has_no_runtime_mutation(store.db_path, existing_run_id)


def test_relative_sidecar_is_resolved_once_before_cwd_changes(tmp_path, monkeypatch):
    working = tmp_path / "working"
    working.mkdir()
    store, run_id = _create_sidecar(working / "sidecar.sqlite3")
    monkeypatch.chdir(working)
    resolved = validate_worker_sidecar_path("sidecar.sqlite3", run_id)
    monkeypatch.chdir(tmp_path)
    assert resolved == store.db_path.resolve()
    assert JobStore(resolved).db_path == store.db_path.resolve()


@pytest.mark.parametrize(
    ("main_factory", "expected"),
    [
        (lambda path: path, "file does not exist"),
        (lambda path: path.write_bytes(b"not sqlite") or path, "SQLite validation failed"),
    ],
)
def test_invalid_main_db_fails_before_registration_claims_and_client(
    tmp_path, main_factory, expected
):
    sidecar, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3")
    main_db = tmp_path / "invalid-main.sqlite3"
    result = main_factory(main_db)
    if not isinstance(result, Path):
        result = main_db
    vocabulary_db = tmp_path / "isolated" / "vocabulary.sqlite3"
    constructed_before = FakeClient.constructed

    with pytest.raises(WorkerRuntimePathError, match=expected):
        run_worker_loop(
            "test-model",
            target_run_id=run_id,
            max_claims=1,
            store=sidecar,
            main_db_path=result,
            vocabulary_db_path=vocabulary_db,
            ollama_client_factory=FakeClient,
        )

    assert _counts(sidecar, run_id) == {
        "pending": 1,
        "running": 0,
        "done": 0,
        "failed": 0,
        "attempts": 0,
        "running_workers": 0,
    }
    assert FakeClient.constructed == constructed_before
    assert not vocabulary_db.exists()
    assert _worker_rows(sidecar, run_id) == 0


def test_main_db_missing_required_tables_fails_before_registration(tmp_path):
    sidecar, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3")
    main_db = _create_main_db(tmp_path / "main.sqlite3", include_required_tables=False)
    vocabulary_db = tmp_path / "vocabulary.sqlite3"
    with pytest.raises(WorkerRuntimePathError, match="required canonical tables"):
        run_worker_loop(
            "test-model",
            target_run_id=run_id,
            store=sidecar,
            main_db_path=main_db,
            vocabulary_db_path=vocabulary_db,
            ollama_client_factory=FakeClient,
        )
    assert _counts(sidecar, run_id)["attempts"] == 0
    assert _counts(sidecar, run_id)["running_workers"] == 0
    assert not vocabulary_db.exists()


def test_main_and_vocabulary_paths_must_differ(tmp_path):
    main_db = _create_main_db(tmp_path / "main.sqlite3")
    with pytest.raises(WorkerRuntimePathError, match="must differ"):
        validate_worker_runtime_paths(main_db, main_db)


def test_main_and_vocabulary_hard_links_must_differ(tmp_path):
    main_db = _create_main_db(tmp_path / "main.sqlite3")
    vocabulary_alias = tmp_path / "vocabulary-alias.sqlite3"
    os.link(main_db, vocabulary_alias)
    with pytest.raises(WorkerRuntimePathError, match="must differ"):
        validate_worker_runtime_paths(main_db, vocabulary_alias)


@pytest.mark.parametrize("symlink_target", ["file", "parent"])
def test_main_db_rejects_symlink_components(tmp_path, symlink_target):
    actual_dir = tmp_path / "actual"
    actual_dir.mkdir()
    actual_main = _create_main_db(actual_dir / "main.sqlite3")
    if symlink_target == "file":
        supplied_main = tmp_path / "main-link.sqlite3"
        supplied_main.symlink_to(actual_main)
    else:
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(actual_dir, target_is_directory=True)
        supplied_main = linked_parent / "main.sqlite3"
    with pytest.raises(WorkerRuntimePathError, match="symbolic links"):
        validate_worker_runtime_paths(supplied_main, tmp_path / "vocabulary.sqlite3")


@pytest.mark.parametrize("symlink_target", ["file", "parent"])
def test_vocabulary_db_rejects_symlink_components(tmp_path, symlink_target):
    main_db = _create_main_db(tmp_path / "main.sqlite3")
    actual_dir = tmp_path / "actual-vocabulary"
    actual_dir.mkdir()
    if symlink_target == "file":
        actual_vocabulary = actual_dir / "vocabulary.sqlite3"
        actual_vocabulary.touch()
        supplied_vocabulary = tmp_path / "vocabulary-link.sqlite3"
        supplied_vocabulary.symlink_to(actual_vocabulary)
    else:
        linked_parent = tmp_path / "linked-vocabulary-parent"
        linked_parent.symlink_to(actual_dir, target_is_directory=True)
        supplied_vocabulary = linked_parent / "vocabulary.sqlite3"
    with pytest.raises(WorkerRuntimePathError, match="symbolic links"):
        validate_worker_runtime_paths(main_db, supplied_vocabulary)


def test_unreadable_main_db_is_rejected(tmp_path):
    main_db = _create_main_db(tmp_path / "main.sqlite3")
    main_db.chmod(0o200)
    try:
        with pytest.raises(WorkerRuntimePathError, match="file is not readable"):
            validate_worker_runtime_paths(main_db, tmp_path / "vocabulary.sqlite3")
    finally:
        main_db.chmod(0o600)


def test_canonical_vocabulary_path_is_rejected(tmp_path):
    main_db = _create_main_db(tmp_path / "main.sqlite3")
    with pytest.raises(WorkerRuntimePathError, match="canonical vocabulary path"):
        validate_worker_runtime_paths(main_db, CANONICAL_VOCABULARY_PATH)


def test_relative_runtime_paths_are_resolved_once(tmp_path, monkeypatch):
    working = tmp_path / "working"
    working.mkdir()
    _create_main_db(working / "main.sqlite3")
    monkeypatch.chdir(working)
    paths = validate_worker_runtime_paths("main.sqlite3", "isolated/vocabulary.sqlite3")
    assert paths.main_db_path == (working / "main.sqlite3").resolve()
    assert paths.vocabulary_db_path == (working / "isolated/vocabulary.sqlite3").resolve()
    assert paths.main_db_uri.startswith("file://")


def test_unwritable_vocabulary_parent_fails_without_creating_database(tmp_path):
    main_db = _create_main_db(tmp_path / "main.sqlite3")
    parent = tmp_path / "unwritable"
    parent.mkdir()
    parent.chmod(0o500)
    vocabulary_db = parent / "vocabulary.sqlite3"
    try:
        with pytest.raises(WorkerRuntimePathError, match="parent is not writable"):
            validate_worker_runtime_paths(main_db, vocabulary_db)
        assert not vocabulary_db.exists()
    finally:
        parent.chmod(0o700)


def test_legacy_target_done_stops_after_successes(tmp_path):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3", jobs=2)
    main_db, vocabulary_db = _run_paths(tmp_path)
    run_worker_loop(
        "test-model",
        target_run_id=run_id,
        target_done=1,
        store=store,
        main_db_path=main_db,
        vocabulary_db_path=vocabulary_db,
        ollama_client_factory=FakeClient,
    )
    counts = _counts(store, run_id)
    assert counts["done"] == 1
    assert counts["pending"] == 1
    assert counts["running_workers"] == 0


def test_target_terminal_stops_naturally_after_all_failed_jobs(tmp_path):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3", jobs=2)
    main_db, vocabulary_db = _run_paths(tmp_path)
    run_worker_loop(
        "test-model",
        target_run_id=run_id,
        target_terminal=2,
        store=store,
        main_db_path=main_db,
        vocabulary_db_path=vocabulary_db,
        ollama_client_factory=FailingClient,
    )
    counts = _counts(store, run_id)
    assert counts["failed"] == 2
    assert counts["pending"] == 0
    assert counts["attempts"] == 2
    assert counts["running_workers"] == 0


def test_max_claims_is_independent_from_terminal_target(tmp_path):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3", jobs=3)
    main_db, vocabulary_db = _run_paths(tmp_path)
    run_worker_loop(
        "test-model",
        target_run_id=run_id,
        target_terminal=3,
        max_claims=1,
        store=store,
        main_db_path=main_db,
        vocabulary_db_path=vocabulary_db,
        ollama_client_factory=FailingClient,
    )
    counts = _counts(store, run_id)
    assert counts["failed"] == 1
    assert counts["pending"] == 2
    assert counts["attempts"] == 1


def test_neither_target_option_preserves_unbounded_default(tmp_path):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3", jobs=2)
    main_db, vocabulary_db = _run_paths(tmp_path)
    shutdown = threading.Event()

    class ShutdownClient(FakeClient):
        def generate_tags(self, *args, **kwargs):
            result = super().generate_tags(*args, **kwargs)
            shutdown.set()
            return result

    run_worker_loop(
        "test-model",
        target_run_id=run_id,
        store=store,
        main_db_path=main_db,
        vocabulary_db_path=vocabulary_db,
        ollama_client_factory=ShutdownClient,
        shutdown_event=shutdown,
    )
    counts = _counts(store, run_id)
    assert counts["done"] == 1
    assert counts["pending"] == 1
    assert counts["running_workers"] == 0


def test_startup_failure_and_idempotent_cleanup_leave_no_running_state(tmp_path):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3")
    main_db, vocabulary_db = _run_paths(tmp_path)

    def fail_startup(_model):
        raise RuntimeError("synthetic startup failure")

    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        run_worker_loop(
            "test-model",
            target_run_id=run_id,
            store=store,
            main_db_path=main_db,
            vocabulary_db_path=vocabulary_db,
            ollama_client_factory=fail_startup,
        )
    assert _counts(store, run_id)["running_workers"] == 0
    mark_worker_inactive(store, run_id, "w1")
    mark_worker_inactive(store, run_id, "w1")
    assert _counts(store, run_id)["running_workers"] == 0


def test_handled_shutdown_marks_registered_worker_inactive(tmp_path):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3")
    main_db, vocabulary_db = _run_paths(tmp_path)
    shutdown = threading.Event()
    shutdown.set()
    run_worker_loop(
        "test-model",
        target_run_id=run_id,
        store=store,
        main_db_path=main_db,
        vocabulary_db_path=vocabulary_db,
        ollama_client_factory=FakeClient,
        shutdown_event=shutdown,
    )
    counts = _counts(store, run_id)
    assert counts["attempts"] == 0
    assert counts["pending"] == 1
    assert counts["running_workers"] == 0


@pytest.mark.parametrize("shutdown_signal", [signal.SIGTERM, signal.SIGINT])
def test_process_signal_requests_orderly_registered_shutdown(tmp_path, shutdown_signal):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3", jobs=2)
    main_db, vocabulary_db = _run_paths(tmp_path)

    class SignallingClient(FakeClient):
        def generate_tags(self, *args, **kwargs):
            result = super().generate_tags(*args, **kwargs)
            os.kill(os.getpid(), shutdown_signal)
            return result

    run_worker_loop(
        "test-model",
        target_run_id=run_id,
        store=store,
        main_db_path=main_db,
        vocabulary_db_path=vocabulary_db,
        ollama_client_factory=SignallingClient,
    )
    counts = _counts(store, run_id)
    assert counts["done"] == 1
    assert counts["pending"] == 1
    assert counts["running_workers"] == 0


def test_exact_estimator_validation_precedes_client_construction(tmp_path):
    store, run_id = _create_sidecar(tmp_path / "sidecar.sqlite3", model="qwen3:14b")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE tagging_run SET unit_strategy_version = ?, settings_json = ? WHERE run_id = ?",
            (
                "unit-v3-prompt-budgeted-chunks",
                json.dumps({"prompt_estimator_version": "unsupported-estimator"}),
                run_id,
            ),
        )
    main_db, vocabulary_db = _run_paths(tmp_path)
    constructed_before = FakeClient.constructed
    run_worker_loop(
        "qwen3:14b",
        target_run_id=run_id,
        store=store,
        main_db_path=main_db,
        vocabulary_db_path=vocabulary_db,
        ollama_client_factory=FakeClient,
    )
    assert FakeClient.constructed == constructed_before
    assert _counts(store, run_id)["attempts"] == 0
    assert _counts(store, run_id)["running_workers"] == 0


def test_cli_rejects_both_stop_targets(capsys):
    with pytest.raises(SystemExit) as error:
        _call_cli(
            [
                "worker",
                "--run-id",
                "run",
                "--db-path",
                "sidecar.sqlite3",
                "--main-db",
                "main.sqlite3",
                "--vocabulary-db",
                "vocabulary.sqlite3",
                "--target-done",
                "1",
                "--target-terminal",
                "1",
            ]
        )
    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def _call_cli(arguments: list[str]) -> None:
    original = sys.argv
    sys.argv = ["semantic-tagger", *arguments]
    try:
        cli.main()
    finally:
        sys.argv = original


def test_real_cli_worker_orchestration_from_unrelated_cwd(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    main_db = _create_main_db(assets / "main.sqlite3")
    store, run_id = _create_sidecar(
        assets / "sidecar.sqlite3",
        schema_version="semantic-tags-v3",
        prompt_version="semantic-hybrid-v3",
    )
    vocabulary_db = assets / "isolated-vocabulary.sqlite3"
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()

    FakeV3Client.constructed = 0
    FakeV3Client.calls = 0
    monkeypatch.setattr("scripts.semantic_tagger.worker_loop.OllamaClient", FakeV3Client)
    monkeypatch.chdir(unrelated_cwd)
    _call_cli(
        [
            "worker",
            "--run-id",
            run_id,
            "--db-path",
            str(store.db_path.resolve()),
            "--main-db",
            str(main_db.resolve()),
            "--vocabulary-db",
            str(vocabulary_db.resolve()),
            "--model",
            "test-model",
            "--target-terminal",
            "1",
        ]
    )

    assert _counts(store, run_id)["done"] == 1
    assert _counts(store, run_id)["running_workers"] == 0
    assert FakeV3Client.constructed == 1
    assert FakeV3Client.calls == 1
    assert vocabulary_db.is_file()
    with sqlite3.connect(vocabulary_db) as conn:
        assert conn.execute("SELECT count(*) FROM vocabulary_candidate").fetchone()[0] == 3
    assert not (unrelated_cwd / "data" / "mnemosyne.sqlite3").exists()
    assert not (unrelated_cwd / "data" / "semantic_vocabulary.local.sqlite3").exists()
