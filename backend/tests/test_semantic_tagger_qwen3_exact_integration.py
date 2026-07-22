import argparse
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from backend.tests.test_semantic_tagger_qwen3_tokenizer import (
    _independent_bytes_to_unicode,
    _synthetic_tokenizer_values,
    _write_synthetic_model_store,
)
from scripts.semantic_tagger.prompt_budget import (
    PromptBudgetConfig,
    PromptBudgetError,
    estimate_prompt_tokens,
)
from scripts.semantic_tagger.prompt_builder import (
    build_supported_final_prompt_variants,
)
from scripts.semantic_tagger.qwen3_tokenizer import (
    EXACT_PROMPT_ESTIMATOR_VERSION,
    ExactTokenizerContractError,
    estimate_server_prompt_tokens,
    resolve_exact_tokenizer_contract,
)
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.unit_planner import (
    PROMPT_BUDGET_STRATEGY_VERSION,
    PromptBudgetUnitPlanner,
    plan_manifest_context_once,
)
from scripts.semantic_tagger.worker import build_worker_final_prompt


def _compression_merges(texts: list[str]) -> list[str]:
    from scripts.semantic_tagger.qwen3_tokenizer import qwen2_pretokens

    byte_encoder = _independent_bytes_to_unicode()
    encoded_pretokens = {
        "".join(byte_encoder[value] for value in pretoken.encode("utf-8"))
        for text in texts
        for pretoken in qwen2_pretokens(text)
    }
    merges: list[str] = []
    ranks: dict[tuple[str, str], int] = {}

    def reduce_word(encoded: str) -> tuple[str, ...]:
        word = tuple(encoded)
        while len(word) > 1:
            ranked = [(ranks[pair], pair) for pair in zip(word, word[1:]) if pair in ranks]
            if not ranked:
                break
            _, selected = min(ranked)
            reduced = []
            index = 0
            while index < len(word):
                if index + 1 < len(word) and (word[index], word[index + 1]) == selected:
                    reduced.append(word[index] + word[index + 1])
                    index += 2
                else:
                    reduced.append(word[index])
                    index += 1
            word = tuple(reduced)
        return word

    for encoded in sorted(encoded_pretokens):
        while len(word := reduce_word(encoded)) > 1:
            pair = (word[0], word[1])
            assert pair not in ranks
            ranks[pair] = len(merges)
            merges.append(f"{pair[0]} {pair[1]}")
    return merges


def _event(event_id: str, text: str, timestamp: str, role: str = "user") -> dict:
    return {
        "event_id": event_id,
        "context_id": "ctx",
        "text": text,
        "timestamp_start": timestamp,
        "event_type": "chatgpt_message",
        "source_role": role,
    }


def _compressed_exact_contract(tmp_path: Path):
    canonical_content = (
        "[CONTEXT_TITLE]\n[/CONTEXT_TITLE]\n\n[EVENT event_id=e1 role=user]\nx\n[/EVENT]"
    )
    prompts = build_supported_final_prompt_variants("semantic-hybrid-v3", canonical_content, ["e1"])
    merges = _compression_merges([result.prompt for result in prompts.values()])
    root, pins, _, _ = _write_synthetic_model_store(
        tmp_path,
        values=_synthetic_tokenizer_values(merges=merges),
    )
    return resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)


def _exact_planner(tmp_path: Path, *, max_prompt_tokens: int = 5632):
    contract = _compressed_exact_contract(tmp_path)
    budget = PromptBudgetConfig(
        max_prompt_tokens=max_prompt_tokens,
        num_predict=0,
        safety_margin=0,
        prompt_estimator_version=EXACT_PROMPT_ESTIMATOR_VERSION,
        prompt_estimator_contract=contract,
    )
    return (
        PromptBudgetUnitPlanner(
            prompt_version="semantic-hybrid-v3",
            schema_version="semantic-tags-v3",
            budget=budget,
            overlap_events=0,
        ),
        contract,
        budget,
    )


def test_exact_planner_budgets_all_worker_retry_variants_and_splits(tmp_path):
    planner, contract, _ = _exact_planner(tmp_path)
    low = 1
    high = 10_000
    best = None
    while low <= high:
        repetitions = (low + high) // 2
        event = _event("e1", "x\n" * repetitions, "1")
        segment_type = __import__(
            "scripts.semantic_tagger.unit_planner",
            fromlist=["_SegmentInput"],
        )._SegmentInput
        assessment = planner._measure(
            "ctx",
            [segment_type(event=event, start_char=0, end_char=len(event["text"]))],
            "",
        )[2]
        if assessment.initial_prompt_estimate <= 5632:
            best = (event, assessment)
            low = repetitions + 1
        else:
            high = repetitions - 1
    assert best is not None
    event, assessment = best
    assert assessment.initial_prompt_estimate <= 5632
    assert assessment.maximum_retry_prompt_estimate > 5632

    units = planner.build_units_for_context("ctx", [event])
    assert len(units) > 1
    assert all(unit["worst_case_prompt_estimate"] <= 5632 for unit in units)
    assert all(
        unit["segments"]["prompt_estimator_contract"] == contract.as_manifest() for unit in units
    )
    assert all(
        set(unit["variant_prompt_estimates"])
        == {
            "attempt_1",
            "attempt_2_schema_retry",
            "attempt_2_facets_missing",
            "attempt_3_reduced_output",
        }
        for unit in units
    )


def test_exact_planner_and_worker_use_identical_prompts_for_all_variants(tmp_path):
    planner, contract, _ = _exact_planner(tmp_path)
    event = _event("e1", "synthetic code: https://example.invalid\nERROR 3", "1")
    unit = planner.build_units_for_context("ctx", [event])[0]
    from scripts.semantic_tagger.unit_serializer import serialize_semantic_unit

    content = serialize_semantic_unit(unit["segments"], [event["text"]])
    supported = build_supported_final_prompt_variants("semantic-hybrid-v3", content, ["e1"])
    for variant_name, result in supported.items():
        variant_parts = {
            "attempt_1": (1, None),
            "attempt_2_schema_retry": (2, None),
            "attempt_2_facets_missing": (2, "facets_missing"),
            "attempt_3_reduced_output": (3, None),
        }
        attempt_no, retry_reason = variant_parts[variant_name]
        worker = build_worker_final_prompt(
            {
                "prompt_version": "semantic-hybrid-v3",
                "attempt_count": attempt_no,
                "retry_reason": retry_reason,
            },
            type("Unit", (), {"content": content, "event_ids": ("e1",)})(),
        )
        assert worker.prompt.encode("utf-8") == result.prompt.encode("utf-8")
        assert unit["variant_prompt_estimates"][variant_name] == (
            estimate_server_prompt_tokens(worker.prompt, contract)
        )


def _write_main_db(path: Path, events: list[dict]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, text TEXT, "
            "title TEXT, timestamp_start TEXT, event_type TEXT)"
        )
        connection.execute(
            "CREATE TABLE chatgpt_messages (event_id TEXT UNIQUE, role TEXT NOT NULL)"
        )
        for event in events:
            connection.execute(
                "INSERT INTO events VALUES (?, 'ctx', ?, '', ?, 'chatgpt_message')",
                (event["event_id"], event["text"], event["timestamp_start"]),
            )
            connection.execute(
                "INSERT INTO chatgpt_messages VALUES (?, ?)",
                (event["event_id"], event["source_role"]),
            )


def test_exact_queue_accepts_authoritative_unit_and_rejects_forged_estimate(tmp_path, monkeypatch):
    from scripts.semantic_tagger.job_store import JobStore

    planner, contract, budget = _exact_planner(tmp_path / "tokenizer")
    event = _event("e1", "synthetic queue content", "1")
    unit = planner.build_units_for_context("ctx", [event])[0]
    main_db = tmp_path / "main.sqlite3"
    _write_main_db(main_db, [event])
    monkeypatch.setattr(
        "scripts.semantic_tagger.qwen3_tokenizer.resolve_exact_tokenizer_contract",
        lambda model_name: contract,
    )

    valid_sidecar = tmp_path / "valid.sqlite3"
    valid_store = JobStore(valid_sidecar)
    valid_store.save_unit(unit, require_unique=True)
    run_id = valid_store.create_run(
        {
            "model_name": "qwen3:14b",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": budget.as_settings(),
        }
    )
    recalculated = valid_store.queue_v3_unit_job(
        "valid-exact-job",
        run_id,
        unit["unit_id"],
        unit["content_hash"],
        main_db_uri=f"file:{main_db}?mode=ro",
    )
    assert recalculated == unit["worst_case_prompt_estimate"]
    with sqlite3.connect(valid_sidecar) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tagging_job").fetchone()[0] == 1
        settings = json.loads(
            connection.execute("SELECT settings_json FROM tagging_run").fetchone()[0]
        )
    assert settings["prompt_estimator_contract"] == contract.as_manifest()

    forged_sidecar = tmp_path / "forged.sqlite3"
    forged_store = JobStore(forged_sidecar)
    forged_store.save_unit(unit, require_unique=True)
    forged_run = forged_store.create_run(
        {
            "model_name": "qwen3:14b",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": budget.as_settings(),
        }
    )
    with sqlite3.connect(forged_sidecar) as connection:
        connection.execute(
            "UPDATE tagging_unit SET estimated_token_count=1 WHERE unit_id=?",
            (unit["unit_id"],),
        )
    with pytest.raises(ValueError, match="authoritative recalculation"):
        forged_store.queue_v3_unit_job(
            "forged-exact-job",
            forged_run,
            unit["unit_id"],
            unit["content_hash"],
            main_db_uri=f"file:{main_db}?mode=ro",
        )
    with sqlite3.connect(forged_sidecar) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tagging_job").fetchone()[0] == 0


def test_exact_queue_rejects_tokenizer_template_contract_drift(tmp_path, monkeypatch):
    from scripts.semantic_tagger.job_store import JobStore

    planner, contract, budget = _exact_planner(tmp_path / "tokenizer")
    event = _event("e1", "synthetic drift content", "1")
    unit = planner.build_units_for_context("ctx", [event])[0]
    main_db = tmp_path / "main.sqlite3"
    _write_main_db(main_db, [event])
    monkeypatch.setattr(
        "scripts.semantic_tagger.qwen3_tokenizer.resolve_exact_tokenizer_contract",
        lambda model_name: contract,
    )
    sidecar = tmp_path / "drift.sqlite3"
    store = JobStore(sidecar)
    store.save_unit(unit, require_unique=True)
    run_id = store.create_run(
        {
            "model_name": "qwen3:14b",
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "settings": budget.as_settings(),
        }
    )
    drifted = replace(contract, ollama_template_sha256="0" * 64)
    monkeypatch.setattr(
        "scripts.semantic_tagger.qwen3_tokenizer.resolve_exact_tokenizer_contract",
        lambda model_name: drifted,
    )
    with pytest.raises(PromptBudgetError, match="does not match"):
        store.queue_v3_unit_job(
            "drifted-exact-job",
            run_id,
            unit["unit_id"],
            unit["content_hash"],
            main_db_uri=f"file:{main_db}?mode=ro",
        )
    with sqlite3.connect(sidecar) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tagging_job").fetchone()[0] == 0


def test_worker_launch_contract_check_fails_closed_before_client_use(tmp_path, monkeypatch):
    from scripts.semantic_tagger.worker_loop import (
        validate_worker_prompt_estimator_contract,
    )

    _, contract, budget = _exact_planner(tmp_path)
    run_row = {
        "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
        "model_name": "qwen3:14b",
        "settings_json": json.dumps(budget.as_settings()),
    }
    monkeypatch.setattr(
        "scripts.semantic_tagger.qwen3_tokenizer.resolve_exact_tokenizer_contract",
        lambda model_name: contract,
    )
    validate_worker_prompt_estimator_contract(run_row)

    drifted = replace(contract, tokenizer_merge_array_sha256="0" * 64)
    monkeypatch.setattr(
        "scripts.semantic_tagger.qwen3_tokenizer.resolve_exact_tokenizer_contract",
        lambda model_name: drifted,
    )
    with pytest.raises(PromptBudgetError, match="does not match"):
        validate_worker_prompt_estimator_contract(run_row)


def test_exact_dry_run_contract_matches_production_manifest_planning(tmp_path):
    from scripts.semantic_tagger.cli import _stage_v3_manifest_contexts

    planner, _, _ = _exact_planner(tmp_path)
    events = [
        _event("e1", "first", "1"),
        _event("e2", "second", "2"),
        _event("e3", "third", "3"),
    ]
    dry_run = plan_manifest_context_once(
        planner,
        "ctx",
        events,
        "",
        [["e1", "e2"], ["e2", "e3"]],
    )

    class Rows:
        pass

    source_items = [
        {
            "manifest_entry": {"context_id": "ctx"},
            "reconstructed_old": type("Source", (), {"event_ids": group})(),
        }
        for group in (("e1", "e2"), ("e2", "e3"))
    ]
    connection = Rows()
    from unittest.mock import patch

    with patch(
        "scripts.semantic_tagger.cli.load_events_for_context",
        return_value=events,
    ):
        production = _stage_v3_manifest_contexts(planner, source_items, connection)["units"]
    assert [
        (unit["unit_id"], unit["content_hash"], unit["sequence_no"]) for unit in production
    ] == [(unit["unit_id"], unit["content_hash"], unit["sequence_no"]) for unit in dry_run]


def test_exact_unavailable_fails_before_target_sidecar_creation(tmp_path, monkeypatch):
    from scripts.semantic_tagger.cli import cmd_prepare_evaluation

    event = _event("e1", "synthetic source", "1")
    main_db = tmp_path / "main.sqlite3"
    _write_main_db(main_db, [event])
    source_unit = UnitBuilder(
        schema_version="semantic-tags-v2",
        strategy_version="unit-v2-whole-events",
    ).build_units_for_context("ctx", [event])[0]
    source_sidecar = tmp_path / "source.sqlite3"
    with sqlite3.connect(source_sidecar) as connection:
        connection.execute(
            "CREATE TABLE tagging_run (run_id TEXT, schema_version TEXT, "
            "unit_strategy_version TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO tagging_run VALUES "
            "('source', 'semantic-tags-v2', 'unit-v2-whole-events', '1')"
        )
        connection.execute(
            "CREATE TABLE tagging_unit (unit_id TEXT, context_id TEXT, "
            "segments_json TEXT, content_hash TEXT)"
        )
        connection.execute(
            "INSERT INTO tagging_unit VALUES (?, 'ctx', ?, ?)",
            (
                source_unit["unit_id"],
                json.dumps(source_unit["segments"]),
                source_unit["content_hash"],
            ),
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "unit_id": source_unit["unit_id"],
                    "context_id": "ctx",
                    "content_hash": source_unit["content_hash"],
                }
            ]
        ),
        encoding="utf-8",
    )
    target_sidecar = tmp_path / "target.sqlite3"
    args = argparse.Namespace(
        source_sidecar=str(source_sidecar),
        source_run_id=None,
        manifest=str(manifest),
        target_sidecar=str(target_sidecar),
        model="qwen3:14b",
        target_schema_version="semantic-tags-v3",
        target_prompt_version="semantic-hybrid-v3",
        target_unit_strategy_version=PROMPT_BUDGET_STRATEGY_VERSION,
        think=False,
        stream=False,
        temperature=0.0,
        seed=42,
        num_predict=1536,
        num_ctx=8192,
        max_prompt_tokens=5632,
        safety_margin=1024,
        prompt_estimator_version=EXACT_PROMPT_ESTIMATOR_VERSION,
        chunk_overlap_characters=256,
        chunk_boundary_backtrack_characters=256,
        request_timeout_seconds=3600,
        expected_units=1,
        expected_contexts=1,
    )
    monkeypatch.setattr(
        "scripts.semantic_tagger.cli.get_main_db",
        lambda: sqlite3.connect(main_db),
    )
    monkeypatch.setattr(
        "scripts.semantic_tagger.content_loader.MAIN_DB_URI",
        f"file:{main_db}?mode=ro",
    )

    def unavailable(model_name):
        raise ExactTokenizerContractError("synthetic missing contract")

    monkeypatch.setattr(
        "scripts.semantic_tagger.qwen3_tokenizer.resolve_exact_tokenizer_contract",
        unavailable,
    )
    with pytest.raises(PromptBudgetError, match="unavailable"):
        cmd_prepare_evaluation(args)
    assert not target_sidecar.exists()


def test_v2_estimator_and_unit_v2_identity_remain_unchanged_with_exact_support():
    prompt = "zażółć synthetic"
    assert estimate_prompt_tokens(prompt) == (13 * len(prompt.encode("utf-8")) + 39) // 40
    units = UnitBuilder(max_events=1).build_units_for_context(
        "ctx", [_event("e1", "synthetic", "1")]
    )
    assert units[0]["segments"]["unit_strategy_version"] == "unit-v2-whole-events"
    assert "prompt_estimator_contract" not in units[0]["segments"]
