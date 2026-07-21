import argparse
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.semantic_tagger.cli import _stage_v3_manifest_contexts, cmd_prepare_evaluation
from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit
from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.planner_dry_run import select_inferable_v2_manifest
from scripts.semantic_tagger.prompt_budget import (
    PROMPT_ESTIMATOR_VERSION,
    PromptBudgetConfig,
    PromptBudgetError,
    estimate_supported_prompt_variants,
)
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.unit_planner import (
    PROMPT_BUDGET_STRATEGY_VERSION,
    PromptBudgetUnitPlanner,
    plan_manifest_context_once,
)
from scripts.semantic_tagger.unit_serializer import (
    compute_v3_content_hash,
    serialize_semantic_unit,
)


def _event(
    event_id: str,
    text: str,
    timestamp: str,
    role: str = "user",
) -> dict:
    return {
        "event_id": event_id,
        "context_id": "ctx",
        "text": text,
        "timestamp_start": timestamp,
        "event_type": "chatgpt_message",
        "source_role": role,
    }


def _planner(
    *,
    max_prompt_tokens: int = 5632,
    max_events: int = 10,
    overlap_events: int = 1,
    backtrack: int = 256,
) -> PromptBudgetUnitPlanner:
    budget = PromptBudgetConfig(
        num_ctx=8192,
        max_prompt_tokens=max_prompt_tokens,
        num_predict=0,
        safety_margin=0,
        chunk_overlap_characters=256,
        chunk_boundary_backtrack_characters=backtrack,
    )
    return PromptBudgetUnitPlanner(
        prompt_version="semantic-hybrid-v3",
        schema_version="semantic-tags-v3",
        budget=budget,
        max_events=max_events,
        overlap_events=overlap_events,
    )


def planner_segment(event: dict, start: int, end: int):
    from scripts.semantic_tagger.unit_planner import _SegmentInput

    return _SegmentInput(event=event, start_char=start, end_char=end, is_chunk=True)


def _event_whose_initial_prompt_only_just_fits() -> tuple[dict, object]:
    planner = _planner()
    low = 1
    high = 20000
    best = None
    while low <= high:
        middle = (low + high) // 2
        event = _event("e-retry-boundary", "x" * middle, "1")
        assessment = planner._measure("ctx", [planner_segment(event, 0, middle)], "")[2]
        if assessment.initial_prompt_estimate <= 5632:
            best = (event, assessment)
            low = middle + 1
        else:
            high = middle - 1
    assert best is not None
    return best


def test_middle_slice_is_serialized_and_reconstructed_without_surrounding_text(
    tmp_path, monkeypatch
):
    full_text = "BEFORE|middle-only|AFTER"
    event = _event("canonical-event", full_text, "1", "assistant")
    start = full_text.index("middle-only")
    end = start + len("middle-only")
    planner = _planner()
    unit = planner._prepare_unit("ctx", [planner_segment(event, start, end)], "", sequence_no=1)

    main_db = tmp_path / "main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, text TEXT, "
            "title TEXT, timestamp_start TEXT)"
        )
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, NULL, ?)",
            (event["event_id"], "ctx", full_text, "1"),
        )
    sidecar = tmp_path / "sidecar.sqlite3"
    store = JobStore(sidecar)
    store.save_unit(unit)
    monkeypatch.setattr(
        "scripts.semantic_tagger.content_loader.MAIN_DB_URI",
        f"file:{main_db}?mode=ro",
    )

    reconstructed = load_and_reconstruct_unit(
        unit["unit_id"],
        str(sidecar),
        "semantic-tags-v3",
        PROMPT_BUDGET_STRATEGY_VERSION,
    )

    assert "middle-only" in reconstructed.content
    assert "BEFORE" not in reconstructed.content
    assert "AFTER" not in reconstructed.content
    assert reconstructed.event_ids == ("canonical-event",)
    assert unit["segments"]["segments"][0]["event_id"] == "canonical-event"


def test_v3_settings_are_persisted_and_generic_queue_is_rejected(tmp_path):
    sidecar = tmp_path / "queue.sqlite3"
    store = JobStore(sidecar)
    unit = _planner().build_units_for_context("ctx", [_event("e1", "synthetic", "1")])[0]
    store.save_unit(unit)
    run_id = store.create_run(
        {
            "model_name": "test-model",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": {},
        }
    )

    with sqlite3.connect(sidecar) as conn:
        settings = json.loads(
            conn.execute(
                "SELECT settings_json FROM tagging_run WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )
    assert settings["max_prompt_tokens"] == 5632
    assert settings["num_predict"] == 1536
    assert settings["safety_margin"] == 1024
    assert settings["prompt_estimator_version"] == PROMPT_ESTIMATOR_VERSION
    with pytest.raises(ValueError, match="authoritative queue_v3_unit_job"):
        store.queue_job("job-key", run_id, unit["unit_id"], unit["content_hash"])
    with sqlite3.connect(sidecar) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tagging_job").fetchone()[0] == 0


def test_authoritative_queue_rejects_forged_low_estimate_for_oversized_prompt(tmp_path):
    source_text = "x" * 20000
    main_db = tmp_path / "authoritative-main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, text TEXT, "
            "title TEXT, timestamp_start TEXT)"
        )
        conn.execute("INSERT INTO events VALUES ('e1', 'ctx', ?, NULL, '1')", (source_text,))

    manifest = {
        "title_included": False,
        "title_source": "ctx",
        "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
        "prompt_version": "semantic-hybrid-v3",
        "schema_version": "semantic-tags-v3",
        "prompt_estimator_version": PROMPT_ESTIMATOR_VERSION,
        "chunk_overlap_characters": 256,
        "chunk_boundary_backtrack_characters": 256,
        "segments": [
            {
                "event_id": "e1",
                "context_id": "ctx",
                "role": "user",
                "start_char": 0,
                "end_char": len(source_text),
                "sequence_in_unit": 0,
                "is_overlap": False,
                "is_chunk": False,
                "overlap_from_previous_characters": 0,
            }
        ],
        "oversized_single_event": False,
    }
    canonical_content = serialize_semantic_unit(manifest, [source_text])
    assessment = estimate_supported_prompt_variants("semantic-hybrid-v3", canonical_content, ["e1"])
    manifest["prompt_budget_basis"] = "all_supported_attempts"
    manifest["prompt_budget"] = assessment.as_manifest()
    actual_estimate = assessment.worst_case_prompt_estimate
    assert actual_estimate > 5632
    content_hash = compute_v3_content_hash(
        "semantic-tags-v3",
        PROMPT_BUDGET_STRATEGY_VERSION,
        manifest,
        canonical_content,
    )
    unit = {
        "unit_id": "unit-forged-oversized",
        "context_id": "ctx",
        "sequence_no": 1,
        "content_hash": content_hash,
        "event_ids": ["e1"],
        "segments": manifest,
        "event_count": 1,
        "character_count": len(source_text),
        "estimated_token_count": actual_estimate,
        "first_event_at": "1",
        "last_event_at": "1",
    }
    sidecar = tmp_path / "authoritative-sidecar.sqlite3"
    store = JobStore(sidecar)
    store.save_unit(unit, require_unique=True)
    run_id = store.create_run(
        {
            "model_name": "test-model",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": {},
        }
    )
    with sqlite3.connect(sidecar) as conn:
        conn.execute(
            "UPDATE tagging_unit SET estimated_token_count = 1 WHERE unit_id = ?",
            (unit["unit_id"],),
        )

    with pytest.raises(ValueError, match="authoritative recalculation"):
        store.queue_v3_unit_job(
            "forged-job-key",
            run_id,
            unit["unit_id"],
            unit["content_hash"],
            main_db_uri=f"file:{main_db}?mode=ro",
        )
    with sqlite3.connect(sidecar) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tagging_job").fetchone()[0] == 0


def test_queue_rejects_attempt_1_estimate_when_retry_variant_exceeds_limit(tmp_path):
    event, assessment = _event_whose_initial_prompt_only_just_fits()
    assert assessment.initial_prompt_estimate <= 5632
    assert assessment.worst_case_prompt_estimate > 5632

    planner = _planner()
    diagnostic = planner.build_attempt_1_diagnostic_structure("ctx", [event])[0]
    manifest = json.loads(json.dumps(diagnostic["segments"]))
    manifest["prompt_budget_basis"] = "all_supported_attempts"
    canonical_content = serialize_semantic_unit(manifest, [event["text"]])
    content_hash = compute_v3_content_hash(
        "semantic-tags-v3",
        PROMPT_BUDGET_STRATEGY_VERSION,
        manifest,
        canonical_content,
    )
    diagnostic.update(
        {
            "unit_id": "unit-attempt-1-only-forgery",
            "content_hash": content_hash,
            "segments": manifest,
            "estimated_token_count": assessment.initial_prompt_estimate,
        }
    )

    main_db = tmp_path / "retry-authoritative-main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, text TEXT, "
            "title TEXT, timestamp_start TEXT)"
        )
        conn.execute(
            "INSERT INTO events VALUES (?, 'ctx', ?, NULL, '1')",
            (event["event_id"], event["text"]),
        )
    sidecar = tmp_path / "retry-authoritative-sidecar.sqlite3"
    store = JobStore(sidecar)
    store.save_unit(diagnostic, require_unique=True)
    run_id = store.create_run(
        {
            "model_name": "test-model",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": {},
        }
    )

    with pytest.raises(ValueError, match="stored worst-case estimate"):
        store.queue_v3_unit_job(
            "attempt-1-forged-job-key",
            run_id,
            diagnostic["unit_id"],
            diagnostic["content_hash"],
            main_db_uri=f"file:{main_db}?mode=ro",
        )
    with sqlite3.connect(sidecar) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tagging_job").fetchone()[0] == 0


def test_planner_and_authoritative_queue_use_same_worst_case_estimate(tmp_path):
    event = _event("e1", "synthetic queue parity", "1")
    unit = _planner().build_units_for_context("ctx", [event])[0]
    main_db = tmp_path / "queue-parity-main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, text TEXT, "
            "title TEXT, timestamp_start TEXT)"
        )
        conn.execute("INSERT INTO events VALUES ('e1', 'ctx', ?, NULL, '1')", (event["text"],))
    sidecar = tmp_path / "queue-parity-sidecar.sqlite3"
    store = JobStore(sidecar)
    store.save_unit(unit, require_unique=True)
    run_id = store.create_run(
        {
            "model_name": "test-model",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": {},
        }
    )

    authoritative = store.queue_v3_unit_job(
        "queue-parity-job-key",
        run_id,
        unit["unit_id"],
        unit["content_hash"],
        main_db_uri=f"file:{main_db}?mode=ro",
    )

    assert authoritative == unit["worst_case_prompt_estimate"]
    assert authoritative == unit["estimated_token_count"]


def _evaluation_fixture(tmp_path, text: str):
    main_db = tmp_path / "evaluation-main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, event_type TEXT, "
            "timestamp_start TEXT, title TEXT, text TEXT)"
        )
        conn.execute("CREATE TABLE chatgpt_messages (event_id TEXT UNIQUE, role TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO events VALUES ('e1', 'ctx', 'chatgpt_message', '1', '', ?)",
            (text,),
        )
        conn.execute("INSERT INTO chatgpt_messages VALUES ('e1', 'user')")

    old_unit = UnitBuilder(
        schema_version="semantic-tags-v2",
        strategy_version="unit-v2-whole-events",
    ).build_units_for_context("ctx", [_event("e1", text, "1")])[0]
    source_db = tmp_path / "evaluation-source.sqlite3"
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            "CREATE TABLE tagging_run (run_id TEXT, schema_version TEXT, "
            "unit_strategy_version TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO tagging_run VALUES ('source-run', 'semantic-tags-v2', "
            "'unit-v2-whole-events', '1')"
        )
        conn.execute(
            "CREATE TABLE tagging_unit (unit_id TEXT, context_id TEXT, segments_json TEXT, "
            "content_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO tagging_unit VALUES (?, ?, ?, ?)",
            (
                old_unit["unit_id"],
                "ctx",
                json.dumps(old_unit["segments"]),
                old_unit["content_hash"],
            ),
        )
    manifest = tmp_path / "evaluation-manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "unit_id": old_unit["unit_id"],
                    "context_id": "ctx",
                    "content_hash": old_unit["content_hash"],
                }
            ]
        ),
        encoding="utf-8",
    )
    return main_db, source_db, manifest


def _overlap_evaluation_fixture(tmp_path):
    events = [
        _event("e1", "first", "1"),
        _event("e2", "second", "2"),
        _event("e3", "third", "3"),
    ]
    main_db = tmp_path / "overlap-main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, event_type TEXT, "
            "timestamp_start TEXT, title TEXT, text TEXT)"
        )
        conn.execute("CREATE TABLE chatgpt_messages (event_id TEXT UNIQUE, role TEXT NOT NULL)")
        for event in events:
            conn.execute(
                "INSERT INTO events VALUES (?, 'ctx', 'chatgpt_message', ?, '', ?)",
                (event["event_id"], event["timestamp_start"], event["text"]),
            )
            conn.execute("INSERT INTO chatgpt_messages VALUES (?, 'user')", (event["event_id"],))

    source_units = UnitBuilder(
        max_events=2,
        overlap_events=1,
        schema_version="semantic-tags-v2",
        strategy_version="unit-v2-whole-events",
    ).build_units_for_context("ctx", events)
    assert [unit["event_ids"] for unit in source_units] == [["e1", "e2"], ["e2", "e3"]]
    source_db = tmp_path / "overlap-source.sqlite3"
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            "CREATE TABLE tagging_run (run_id TEXT, schema_version TEXT, "
            "unit_strategy_version TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO tagging_run VALUES ('source-run', 'semantic-tags-v2', "
            "'unit-v2-whole-events', '1')"
        )
        conn.execute(
            "CREATE TABLE tagging_unit (unit_id TEXT, context_id TEXT, segments_json TEXT, "
            "content_hash TEXT)"
        )
        for unit in source_units:
            conn.execute(
                "INSERT INTO tagging_unit VALUES (?, 'ctx', ?, ?)",
                (unit["unit_id"], json.dumps(unit["segments"]), unit["content_hash"]),
            )
    manifest = tmp_path / "overlap-manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "unit_id": unit["unit_id"],
                    "context_id": "ctx",
                    "content_hash": unit["content_hash"],
                }
                for unit in source_units
            ]
        ),
        encoding="utf-8",
    )
    return events, main_db, source_db, manifest


def _evaluation_args(source_db, manifest, target_db, **overrides):
    values = {
        "source_sidecar": str(source_db),
        "source_run_id": None,
        "manifest": str(manifest),
        "target_sidecar": str(target_db),
        "model": "test-model",
        "target_schema_version": "semantic-tags-v3",
        "target_prompt_version": "semantic-hybrid-v3",
        "target_unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
        "think": False,
        "stream": False,
        "temperature": 0.0,
        "seed": 42,
        "num_predict": None,
        "num_ctx": 8192,
        "max_prompt_tokens": 5632,
        "safety_margin": 1024,
        "prompt_estimator_version": PROMPT_ESTIMATOR_VERSION,
        "chunk_overlap_characters": 256,
        "chunk_boundary_backtrack_characters": 256,
        "request_timeout_seconds": 3600,
        "expected_units": 1,
        "expected_contexts": 1,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_official_evaluation_path_prepares_budgeted_chunks_without_inference(
    tmp_path,
):
    main_db, source_db, manifest = _evaluation_fixture(tmp_path, "x" * 25000)
    target_db = tmp_path / "evaluation-target.sqlite3"
    args = _evaluation_args(source_db, manifest, target_db)

    with (
        patch("scripts.semantic_tagger.cli.get_main_db") as get_main,
        patch("scripts.semantic_tagger.content_loader.MAIN_DB_URI", f"file:{main_db}?mode=ro"),
    ):
        get_main.side_effect = lambda: sqlite3.connect(main_db)
        cmd_prepare_evaluation(args)

    with sqlite3.connect(target_db) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM tagging_run").fetchone()
        settings = json.loads(run["settings_json"])
        units = conn.execute("SELECT * FROM tagging_unit ORDER BY sequence_no").fetchall()
        jobs = conn.execute("SELECT * FROM tagging_job").fetchall()
        attempts = conn.execute("SELECT COUNT(*) FROM tagging_attempt").fetchone()[0]

    assert len(units) == len(jobs) > 1
    assert attempts == 0
    assert settings["num_predict"] == 1536
    assert settings["max_prompt_tokens"] == 5632
    assert all(unit["estimated_token_count"] <= 5632 for unit in units)
    assert all(len(json.loads(unit["event_ids_json"])) == 1 for unit in units)
    assert all(len(json.loads(unit["segments_json"])["segments"]) == 1 for unit in units)


def test_overlapping_source_manifest_is_planned_once_with_explicit_unique_jobs_and_lineage(
    tmp_path, capsys
):
    events, main_db, source_db, manifest = _overlap_evaluation_fixture(tmp_path)
    target_db = tmp_path / "overlap-target.sqlite3"
    args = _evaluation_args(
        source_db,
        manifest,
        target_db,
        expected_units=2,
        expected_contexts=1,
    )
    dry_run_units = plan_manifest_context_once(
        _planner(),
        "ctx",
        events,
        "",
        [["e1", "e2"], ["e2", "e3"]],
    )

    with (
        patch("scripts.semantic_tagger.cli.get_main_db") as get_main,
        patch("scripts.semantic_tagger.content_loader.MAIN_DB_URI", f"file:{main_db}?mode=ro"),
    ):
        get_main.side_effect = lambda: sqlite3.connect(main_db)
        cmd_prepare_evaluation(args)
    output = capsys.readouterr().out

    with sqlite3.connect(target_db) as conn:
        conn.row_factory = sqlite3.Row
        target_units = conn.execute("SELECT * FROM tagging_unit ORDER BY sequence_no").fetchall()
        jobs = conn.execute("SELECT * FROM tagging_job ORDER BY unit_id").fetchall()
        lineage = conn.execute("SELECT * FROM tagging_unit_lineage").fetchall()

    assert len(dry_run_units) == len(target_units) == len(jobs) == 1
    assert [unit["unit_id"] for unit in dry_run_units] == [unit["unit_id"] for unit in target_units]
    assert [unit["content_hash"] for unit in dry_run_units] == [
        unit["content_hash"] for unit in target_units
    ]
    target_manifest = json.loads(target_units[0]["segments_json"])
    assert [segment["event_id"] for segment in target_manifest["segments"]] == [
        "e1",
        "e2",
        "e3",
    ]
    assert sum(segment["event_id"] == "e2" for segment in target_manifest["segments"]) == 1
    assert len(lineage) == 2
    assert {row["source_unit_id"] for row in lineage} == {
        json.loads(manifest.read_text())[0]["unit_id"],
        json.loads(manifest.read_text())[1]["unit_id"],
    }
    assert "units = 1" in output
    assert "jobs_pending = 1" in output


def test_corpus_manifest_selection_matches_production_and_excludes_isolated_empty_event(
    tmp_path,
):
    events = [
        _event("e1", "first", "1"),
        _event("e2", "second", "2"),
        _event("e3", "third", "3"),
        _event("e-large", "x" * 100, "4"),
        _event("e-empty", "", "5"),
    ]
    v2_units = UnitBuilder(
        max_chars=50,
        max_events=2,
        overlap_events=1,
        schema_version="semantic-tags-v3",
        strategy_version="unit-v2-whole-events",
    ).build_units_for_context("ctx", events)
    selection = select_inferable_v2_manifest(events, v2_units)

    assert [unit["event_ids"] for unit in selection["selected_units"]][:2] == [
        ["e1", "e2"],
        ["e2", "e3"],
    ]
    assert v2_units[-1]["event_ids"] == ["e-empty"]
    assert v2_units[-1]["character_count"] == 0
    assert selection["excluded_event_count"] == 1
    assert [event["event_id"] for event in selection["selected_events"]] == [
        "e1",
        "e2",
        "e3",
        "e-large",
    ]

    dry_run_units = plan_manifest_context_once(
        _planner(),
        "ctx",
        events,
        "",
        selection["source_event_id_groups"],
    )
    selected_only_units = _planner().build_units_for_context("ctx", selection["selected_events"])
    assert [
        (unit["sequence_no"], unit["unit_id"], unit["content_hash"]) for unit in dry_run_units
    ] == [
        (unit["sequence_no"], unit["unit_id"], unit["content_hash"]) for unit in selected_only_units
    ]
    assert all("e-empty" not in unit["event_ids"] for unit in dry_run_units)

    main_db = tmp_path / "manifest-parity-main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, event_type TEXT, "
            "timestamp_start TEXT, title TEXT, text TEXT)"
        )
        conn.execute("CREATE TABLE chatgpt_messages (event_id TEXT UNIQUE, role TEXT NOT NULL)")
        for event in events:
            conn.execute(
                "INSERT INTO events VALUES (?, 'ctx', 'chatgpt_message', ?, '', ?)",
                (event["event_id"], event["timestamp_start"], event["text"]),
            )
            conn.execute("INSERT INTO chatgpt_messages VALUES (?, 'user')", (event["event_id"],))

    verified_source_units = [
        {
            "manifest_entry": {
                "context_id": "ctx",
                "unit_id": unit["unit_id"],
                "content_hash": unit["content_hash"],
            },
            "reconstructed_old": SimpleNamespace(event_ids=tuple(unit["event_ids"])),
            "old_unit_id": unit["unit_id"],
        }
        for unit in selection["selected_units"]
    ]
    with sqlite3.connect(main_db) as connection:
        connection.row_factory = sqlite3.Row
        production = _stage_v3_manifest_contexts(_planner(), verified_source_units, connection)

    assert [(unit["unit_id"], unit["content_hash"]) for unit in production["units"]] == [
        (unit["unit_id"], unit["content_hash"]) for unit in dry_run_units
    ]
    assert len({unit["unit_id"] for unit in production["units"]}) == len(dry_run_units)

    sidecar = tmp_path / "manifest-parity-target.sqlite3"
    store = JobStore(sidecar)
    run_id = store.create_run(
        {
            "model_name": "test-model",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": {},
        }
    )
    for index, unit in enumerate(production["units"]):
        store.save_unit(unit, require_unique=True)
        store.queue_v3_unit_job(
            f"manifest-parity-job-{index}",
            run_id,
            unit["unit_id"],
            unit["content_hash"],
            main_db_uri=f"file:{main_db}?mode=ro",
        )
    with sqlite3.connect(sidecar) as conn:
        queued_jobs = conn.execute("SELECT COUNT(*) FROM tagging_job").fetchone()[0]
    assert queued_jobs == len(dry_run_units) == len(production["units"])


def test_official_evaluation_rejects_invalid_budget_before_target_creation(tmp_path):
    main_db, source_db, manifest = _evaluation_fixture(tmp_path, "synthetic")
    target_db = tmp_path / "must-not-exist.sqlite3"
    args = _evaluation_args(source_db, manifest, target_db, num_ctx=8191)

    with (
        patch("scripts.semantic_tagger.cli.get_main_db") as get_main,
        patch("scripts.semantic_tagger.content_loader.MAIN_DB_URI", f"file:{main_db}?mode=ro"),
    ):
        get_main.side_effect = lambda: sqlite3.connect(main_db)
        with pytest.raises(PromptBudgetError, match="above num_ctx"):
            cmd_prepare_evaluation(args)

    assert not target_db.exists()
