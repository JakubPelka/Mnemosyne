from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from scripts.semantic_tagger.prompt_budget import (
    PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
    PromptBudgetConfig,
)
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.unit_planner import (
    PromptBudgetUnitPlanner,
    plan_manifest_context_once,
)


def _connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _percentile(values: Iterable[int], probability: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: list[int]) -> dict[str, int | float | None]:
    return {
        "count": len(values),
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "maximum": max(values) if values else None,
    }


def _summary(values: list[int]) -> dict[str, int | float | None]:
    return {
        "count": len(values),
        "minimum": min(values) if values else None,
        "median": _percentile(values, 0.50),
        "maximum": max(values) if values else None,
    }


def _load_events(connection: sqlite3.Connection, context_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT e.event_id, e.context_id, e.title, e.text, e.timestamp_start,
               e.event_type, cm.role AS source_role
        FROM events AS e
        LEFT JOIN chatgpt_messages AS cm ON cm.event_id = e.event_id
        WHERE e.context_id = ?
        ORDER BY e.timestamp_start ASC, e.event_id ASC
        """,
        (context_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _coverage(events: list[dict[str, Any]], units: list[dict[str, Any]]) -> dict[str, Any]:
    lengths = {event["event_id"]: len(event.get("text") or "") for event in events}
    ranges: dict[str, list[tuple[int, int]]] = {event_id: [] for event_id in lengths}
    range_errors = 0
    duplicate_occurrence_units = 0
    for unit in units:
        event_ids = []
        for segment in unit["segments"]["segments"]:
            event_id = segment["event_id"]
            event_ids.append(event_id)
            start = segment["start_char"]
            end = segment["end_char"]
            if event_id not in lengths or not (0 <= start <= end <= lengths[event_id]):
                range_errors += 1
                continue
            ranges[event_id].append((start, end))
        if len(event_ids) != len(set(event_ids)):
            duplicate_occurrence_units += 1

    coverage_errors = 0
    for event_id, length in lengths.items():
        intervals = sorted(ranges[event_id])
        if length == 0:
            if not intervals:
                coverage_errors += 1
            continue
        cursor = 0
        for start, end in intervals:
            if start > cursor:
                break
            cursor = max(cursor, end)
        if cursor != length:
            coverage_errors += 1
    return {
        "complete": coverage_errors == 0 and range_errors == 0,
        "coverage_errors": coverage_errors,
        "range_errors": range_errors,
        "units_with_repeated_event_id": duplicate_occurrence_units,
    }


def _unit_signature(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "unit_id": unit["unit_id"],
            "content_hash": unit["content_hash"],
            "sequence_no": unit["sequence_no"],
            "manifest": unit["segments"],
            "estimate": unit["estimated_prompt_tokens"],
        }
        for unit in units
    ]


def _planning_structure(unit: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Privacy-safe structural signature independent of diagnostic identities."""
    return tuple(
        (
            segment["event_id"],
            segment["start_char"],
            segment["end_char"],
            segment["is_overlap"],
            segment["is_chunk"],
            segment["overlap_from_previous_characters"],
        )
        for segment in unit["segments"]["segments"]
    )


def select_inferable_v2_manifest(
    canonical_events: list[dict[str, Any]],
    v2_units: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select the exact v2 source manifest used by a full inferable batch.

    A v2 unit is inferable precisely when its aggregate character_count is
    positive. Overlapping selected units contribute an ordered event union;
    an event is excluded only when it occurs exclusively in noninferable units.
    """
    selected_units = [unit for unit in v2_units if unit["character_count"] > 0]
    source_event_id_groups = [list(unit["event_ids"]) for unit in selected_units]
    selected_ids = {event_id for event_ids in source_event_id_groups for event_id in event_ids}
    selected_events = [event for event in canonical_events if event["event_id"] in selected_ids]
    canonical_ids = {event["event_id"] for event in canonical_events}
    if not selected_ids.issubset(canonical_ids):
        raise ValueError("Inferable v2 manifest contains a non-canonical event")
    return {
        "selected_units": selected_units,
        "source_event_id_groups": source_event_id_groups,
        "selected_events": selected_events,
        "excluded_event_count": len(canonical_ids - selected_ids),
        "differs_from_all_events": selected_ids != canonical_ids,
    }


def _comparison_sets(
    main_connection: sqlite3.Connection,
    comparison_sidecar: Path,
    calibration: dict[str, Any],
    planner: PromptBudgetUnitPlanner,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with _connect_ro(comparison_sidecar) as sidecar:
        for name, source in calibration["role_aware_ab_units"].items():
            row = sidecar.execute(
                "SELECT context_id, segments_json FROM tagging_unit WHERE unit_id = ?",
                (source["unit_id"],),
            ).fetchone()
            if row is None:
                results[name] = {"planning_error": "source unit unavailable"}
                continue
            source_manifest = json.loads(row["segments_json"])
            source_event_ids = list(
                dict.fromkeys(segment["event_id"] for segment in source_manifest["segments"])
            )
            all_events = _load_events(main_connection, row["context_id"])
            title = next((event["title"] for event in all_events if event.get("title")), "")
            units = plan_manifest_context_once(
                planner,
                row["context_id"],
                all_events,
                title,
                [source_event_ids],
            )
            source_set = set(source_event_ids)
            events = [event for event in all_events if event["event_id"] in source_set]
            coverage = _coverage(events, units)
            unit_prompt_budgets = []
            for unit in units:
                unit_prompt_budgets.append(
                    {
                        "unit_safe_id": _safe_id(unit["unit_id"]),
                        "attempt_1_estimate": unit["initial_prompt_estimate"],
                        "maximum_retry_estimate": unit["maximum_retry_prompt_estimate"],
                        "maximum_retry_variant": unit["maximum_retry_variant"],
                        "worst_case_estimate": unit["worst_case_prompt_estimate"],
                        "worst_case_variant": unit["worst_case_prompt_variant"],
                        "ranges": [
                            {
                                "source_event_safe_id": _safe_id(segment["event_id"]),
                                "start_char": segment["start_char"],
                                "end_char": segment["end_char"],
                                "character_length": (segment["end_char"] - segment["start_char"]),
                                "overlap_characters": segment["overlap_from_previous_characters"],
                            }
                            for segment in unit["segments"]["segments"]
                        ],
                    }
                )
            item: dict[str, Any] = {
                "source_event_count": len(events),
                "source_event_safe_ids": [_safe_id(event["event_id"]) for event in events],
                "resulting_v3_unit_count": len(units),
                "prompt_estimates": [unit["estimated_prompt_tokens"] for unit in units],
                "unit_prompt_budgets": unit_prompt_budgets,
                "chunk_count": sum(unit["contains_chunked_text"] for unit in units),
                "complete_event_range_coverage": coverage["complete"],
                "coverage_errors": coverage["coverage_errors"],
                "range_errors": coverage["range_errors"],
            }
            if name == "bird_list":
                chunk_units = [unit for unit in units if unit["contains_chunked_text"]]
                chunks_by_event: dict[str, list[dict[str, Any]]] = {}
                for unit in chunk_units:
                    segment = unit["segments"]["segments"][0]
                    chunks_by_event.setdefault(_safe_id(segment["event_id"]), []).append(
                        {
                            "start_char": segment["start_char"],
                            "end_char": segment["end_char"],
                            "character_length": (segment["end_char"] - segment["start_char"]),
                            "overlap_characters": segment["overlap_from_previous_characters"],
                            "attempt_1_estimate": unit["initial_prompt_estimate"],
                            "maximum_retry_estimate": unit["maximum_retry_prompt_estimate"],
                            "maximum_retry_variant": unit["maximum_retry_variant"],
                            "worst_case_estimate": unit["worst_case_prompt_estimate"],
                        }
                    )
                item["chunks_by_oversized_source_event"] = chunks_by_event
                item["maximum_estimated_prompt_tokens"] = max(
                    unit["estimated_prompt_tokens"] for unit in units
                )
                item["any_chunk_equals_or_exceeds_5632"] = any(
                    unit["estimated_prompt_tokens"] >= 5632 for unit in chunk_units
                )
            results[name] = item
    return results


def build_dry_run_report(
    *,
    main_db: Path,
    calibration_json: Path,
    comparison_sidecar: Path,
) -> dict[str, Any]:
    calibration = json.loads(calibration_json.read_text(encoding="utf-8"))
    budget = PromptBudgetConfig()
    planner = PromptBudgetUnitPlanner(
        prompt_version="semantic-hybrid-v3",
        schema_version="semantic-tags-v3",
        budget=budget,
    )
    v2_builder = UnitBuilder(
        schema_version="semantic-tags-v3",
        strategy_version="unit-v2-whole-events",
    )

    initial_prompt_estimates: list[int] = []
    maximum_retry_prompt_estimates: list[int] = []
    worst_case_prompt_estimates: list[int] = []
    retry_suffix_overheads: list[int] = []
    chunk_lengths: list[int] = []
    effective_overlaps: list[int] = []
    source_events_chunked: set[str] = set()
    contexts_chunked: set[str] = set()
    whole_event_units = 0
    chunk_only_units = 0
    original_v2_inferable_units = 0
    all_canonical_event_count = 0
    production_manifest_selected_event_count = 0
    excluded_noninferable_event_count = 0
    contexts_with_manifest_all_event_difference = 0
    initial_attempt_diagnostic_unit_count = 0
    initial_attempt_diagnostic_chunk_only_unit_count = 0
    initial_attempt_diagnostic_chunked_source_events: set[str] = set()
    units_where_retry_changed_structure = 0
    contexts_where_retry_changed_structure = 0
    units_with_repeated_event_id = 0
    coverage_errors = 0
    range_errors = 0
    planning_failures: list[dict[str, str]] = []
    target_unit_hashes: dict[str, str] = {}
    duplicate_target_unit_id_count = 0
    deterministic = True

    with _connect_ro(main_db) as connection:
        context_rows = connection.execute(
            "SELECT DISTINCT context_id FROM events WHERE context_id IS NOT NULL "
            "ORDER BY context_id"
        ).fetchall()
        for context_row in context_rows:
            context_id = context_row["context_id"]
            events = _load_events(connection, context_id)
            title = next((event["title"] for event in events if event.get("title")), "")
            try:
                v2_units = v2_builder.build_units_for_context(context_id, events, title)
                selection = select_inferable_v2_manifest(events, v2_units)
                source_event_id_groups = selection["source_event_id_groups"]
                original_v2_inferable_units += len(selection["selected_units"])
                all_canonical_event_count += len(events)
                production_manifest_selected_event_count += len(selection["selected_events"])
                excluded_noninferable_event_count += selection["excluded_event_count"]
                contexts_with_manifest_all_event_difference += int(
                    selection["differs_from_all_events"]
                )
                if source_event_id_groups:
                    first = plan_manifest_context_once(
                        planner,
                        context_id,
                        events,
                        title,
                        source_event_id_groups,
                    )
                    second = plan_manifest_context_once(
                        planner,
                        context_id,
                        events,
                        title,
                        source_event_id_groups,
                    )
                    attempt_1_diagnostic = plan_manifest_context_once(
                        planner,
                        context_id,
                        events,
                        title,
                        source_event_id_groups,
                        attempt_1_diagnostic=True,
                    )
                else:
                    first = []
                    second = []
                    attempt_1_diagnostic = []
            except Exception as exc:
                planning_failures.append(
                    {
                        "context_safe_id": _safe_id(context_id),
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            deterministic = deterministic and _unit_signature(first) == _unit_signature(second)
            initial_attempt_diagnostic_unit_count += len(attempt_1_diagnostic)
            initial_attempt_diagnostic_chunk_only_unit_count += sum(
                unit["contains_chunked_text"] for unit in attempt_1_diagnostic
            )
            for unit in attempt_1_diagnostic:
                if unit["contains_chunked_text"]:
                    initial_attempt_diagnostic_chunked_source_events.add(
                        unit["segments"]["segments"][0]["event_id"]
                    )
            production_structures = Counter(_planning_structure(unit) for unit in first)
            diagnostic_structures = Counter(
                _planning_structure(unit) for unit in attempt_1_diagnostic
            )
            if production_structures != diagnostic_structures:
                contexts_where_retry_changed_structure += 1
                units_where_retry_changed_structure += sum(
                    (production_structures - diagnostic_structures).values()
                )

            initial_prompt_estimates.extend(unit["initial_prompt_estimate"] for unit in first)
            maximum_retry_prompt_estimates.extend(
                unit["maximum_retry_prompt_estimate"] for unit in first
            )
            worst_case_prompt_estimates.extend(unit["worst_case_prompt_estimate"] for unit in first)
            retry_suffix_overheads.extend(
                unit["maximum_retry_prompt_estimate"] - unit["initial_prompt_estimate"]
                for unit in first
            )
            whole_event_units += sum(not unit["contains_chunked_text"] for unit in first)
            chunk_only_units += sum(unit["contains_chunked_text"] for unit in first)
            for unit in first:
                previous_hash = target_unit_hashes.get(unit["unit_id"])
                if previous_hash is not None:
                    duplicate_target_unit_id_count += 1
                else:
                    target_unit_hashes[unit["unit_id"]] = unit["content_hash"]
            coverage = _coverage(selection["selected_events"], first)
            coverage_errors += coverage["coverage_errors"]
            range_errors += coverage["range_errors"]
            units_with_repeated_event_id += coverage["units_with_repeated_event_id"]
            for unit in first:
                if not unit["contains_chunked_text"]:
                    continue
                segment = unit["segments"]["segments"][0]
                source_events_chunked.add(segment["event_id"])
                contexts_chunked.add(context_id)
                chunk_lengths.append(segment["end_char"] - segment["start_char"])
                if segment["overlap_from_previous_characters"] > 0:
                    effective_overlaps.append(segment["overlap_from_previous_characters"])

        comparisons = _comparison_sets(
            connection,
            comparison_sidecar,
            calibration,
            planner,
        )

    if duplicate_target_unit_id_count:
        raise ValueError("Production-parity dry-run produced duplicate target unit IDs")

    return {
        "report_version": "semantic-tagger-unit-v3-planner-dry-run-v2",
        "json_is_source_of_truth": True,
        "privacy": (
            "No source text, titles, complete canonical event IDs, or complete context IDs "
            "are included. Identifiers are truncated SHA-256 values."
        ),
        "provenance": {
            "head_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "execution_timestamp": dt.datetime.now(dt.UTC).isoformat(),
            "main_db_sha256": _sha256_file(main_db),
            "calibration_json_sha256": _sha256_file(calibration_json),
            "comparison_sidecar_sha256": _sha256_file(comparison_sidecar),
            "sqlite_policy": "URI mode=ro and PRAGMA query_only=ON",
        },
        "strategy": {
            "strategy_version": "unit-v3-prompt-budgeted-chunks",
            "source_manifest_planning_contract": "context-ordered-union-plan-once",
            "source_manifest_inferable_rule": "v2 tagging_unit.character_count > 0",
            "prompt_budget_contract": (
                "maximum estimated tokens across attempt_1, attempt_2_schema_retry, "
                "attempt_2_facets_missing, and attempt_3_reduced_output"
            ),
            "prompt_budget_basis": PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
            "retry_structure_change_metric": (
                "count of production unit range/overlap structures absent from the "
                "attempt-1-only diagnostic multiset"
            ),
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            **budget.as_settings(),
        },
        "corpus": {
            "original_inferable_v2_units": original_v2_inferable_units,
            "expected_original_inferable_v2_units": 5091,
            "original_v2_count_matches_expected": original_v2_inferable_units == 5091,
            "all_canonical_event_count": all_canonical_event_count,
            "production_manifest_selected_event_count": (production_manifest_selected_event_count),
            "excluded_noninferable_event_count": excluded_noninferable_event_count,
            "contexts_with_manifest_all_event_difference": (
                contexts_with_manifest_all_event_difference
            ),
            "resulting_v3_unit_count": len(worst_case_prompt_estimates),
            "unique_target_unit_count": len(target_unit_hashes),
            "simulated_unique_target_job_count": len(target_unit_hashes),
            "duplicate_target_unit_id_count": duplicate_target_unit_id_count,
            "whole_event_units": whole_event_units,
            "chunk_only_units": chunk_only_units,
            "source_events_requiring_chunking": len(source_events_chunked),
            "total_chunks": chunk_only_units,
            "contexts_containing_chunked_events": len(contexts_chunked),
            "initial_prompt_estimated_tokens": _distribution(initial_prompt_estimates),
            "maximum_retry_prompt_estimated_tokens": _distribution(maximum_retry_prompt_estimates),
            "worst_case_prompt_estimated_tokens": _distribution(worst_case_prompt_estimates),
            "estimated_prompt_tokens": _distribution(worst_case_prompt_estimates),
            "retry_suffix_overhead_tokens": _distribution(retry_suffix_overheads),
            "maximum_retry_suffix_overhead_tokens": (
                max(retry_suffix_overheads) if retry_suffix_overheads else None
            ),
            "units_exceeding_5632": sum(value > 5632 for value in worst_case_prompt_estimates),
            "units_exceeding_5632_for_any_supported_attempt": sum(
                value > 5632 for value in worst_case_prompt_estimates
            ),
            "initial_attempt_diagnostic_unit_count": (initial_attempt_diagnostic_unit_count),
            "initial_attempt_diagnostic_chunk_only_unit_count": (
                initial_attempt_diagnostic_chunk_only_unit_count
            ),
            "initial_attempt_diagnostic_chunked_source_event_count": len(
                initial_attempt_diagnostic_chunked_source_events
            ),
            "retry_budget_unit_count_delta": (
                len(worst_case_prompt_estimates) - initial_attempt_diagnostic_unit_count
            ),
            "retry_budget_chunk_only_unit_count_delta": (
                chunk_only_units - initial_attempt_diagnostic_chunk_only_unit_count
            ),
            "retry_budget_chunked_source_event_count_delta": (
                len(source_events_chunked) - len(initial_attempt_diagnostic_chunked_source_events)
            ),
            "units_where_retry_suffix_changed_chunking_or_packing": (
                units_where_retry_changed_structure
            ),
            "contexts_where_retry_suffix_changed_chunking_or_packing": (
                contexts_where_retry_changed_structure
            ),
            "chunk_character_length": _summary(chunk_lengths),
            "effective_overlap_characters": _summary(effective_overlaps),
            "units_with_repeated_event_id": units_with_repeated_event_id,
            "deterministic_second_build_equality": deterministic,
            "coverage_errors": coverage_errors,
            "range_errors": range_errors,
            "planning_failure_count": len(planning_failures),
            "planning_failures": planning_failures,
        },
        "ab_event_sets": comparisons,
    }


def render_markdown(data: dict[str, Any]) -> str:
    corpus = data["corpus"]
    initial_estimates = corpus["initial_prompt_estimated_tokens"]
    estimates = corpus["worst_case_prompt_estimated_tokens"]
    chunks = corpus["chunk_character_length"]
    overlaps = corpus["effective_overlap_characters"]
    lines = [
        "# Semantic tagger unit-v3 planner dry-run",
        "",
        "JSON is the source of truth. This Markdown was generated from JSON.",
        "",
        data["privacy"],
        "",
        "## Provenance and contract",
        "",
        f"- HEAD: `{data['provenance']['head_sha']}`",
        f"- Executed: `{data['provenance']['execution_timestamp']}`",
        f"- Strategy: `{data['strategy']['strategy_version']}`",
        f"- Manifest planning: `{data['strategy']['source_manifest_planning_contract']}`.",
        f"- Inferable v2 selection: `{data['strategy']['source_manifest_inferable_rule']}`.",
        f"- Prompt budget: {data['strategy']['prompt_budget_contract']}.",
        f"- Estimator: `{data['strategy']['prompt_estimator_version']}`",
        f"- Runtime allocation: {data['strategy']['max_prompt_tokens']} prompt + "
        f"{data['strategy']['num_predict']} output + {data['strategy']['safety_margin']} "
        f"safety = {data['strategy']['num_ctx']} context tokens.",
        "",
        "## Corpus",
        "",
        f"- Original inferable v2 units: {corpus['original_inferable_v2_units']} "
        f"(expected {corpus['expected_original_inferable_v2_units']}; "
        f"match={corpus['original_v2_count_matches_expected']}).",
        f"- Canonical events: {corpus['all_canonical_event_count']}; selected by the "
        f"production manifest: {corpus['production_manifest_selected_event_count']}; "
        f"excluded as noninferable-only: {corpus['excluded_noninferable_event_count']}.",
        f"- Contexts whose selected manifest union differs from all events: "
        f"{corpus['contexts_with_manifest_all_event_difference']}.",
        f"- Resulting v3 units: {corpus['resulting_v3_unit_count']} "
        f"({corpus['whole_event_units']} whole-event, {corpus['chunk_only_units']} chunk-only).",
        f"- Unique target units / simulated jobs: {corpus['unique_target_unit_count']} / "
        f"{corpus['simulated_unique_target_job_count']}; duplicate IDs: "
        f"{corpus['duplicate_target_unit_id_count']}.",
        f"- Oversized source events: {corpus['source_events_requiring_chunking']}; "
        f"chunks: {corpus['total_chunks']}; contexts: "
        f"{corpus['contexts_containing_chunked_events']}.",
        f"- Attempt-1 estimates p50/p90/p95/p99/max: {initial_estimates['p50']} / "
        f"{initial_estimates['p90']} / {initial_estimates['p95']} / "
        f"{initial_estimates['p99']} / {initial_estimates['maximum']}.",
        f"- Worst-case estimates p50/p90/p95/p99/max: {estimates['p50']} / "
        f"{estimates['p90']} / {estimates['p95']} / {estimates['p99']} / "
        f"{estimates['maximum']}.",
        f"- Units exceeding 5632 for any supported attempt: "
        f"{corpus['units_exceeding_5632_for_any_supported_attempt']}.",
        f"- Maximum measured retry suffix overhead: "
        f"{corpus['maximum_retry_suffix_overhead_tokens']} tokens.",
        f"- Attempt-1-only diagnostic units: "
        f"{corpus['initial_attempt_diagnostic_unit_count']} (delta under retry budgeting: "
        f"{corpus['retry_budget_unit_count_delta']}); diagnostic chunks: "
        f"{corpus['initial_attempt_diagnostic_chunk_only_unit_count']} (delta: "
        f"{corpus['retry_budget_chunk_only_unit_count_delta']}); production units whose "
        f"packing/chunking structure changed under retry budgeting: "
        f"{corpus['units_where_retry_suffix_changed_chunking_or_packing']} across "
        f"{corpus['contexts_where_retry_suffix_changed_chunking_or_packing']} contexts.",
        f"- Chunk lengths min/median/max: {chunks['minimum']} / {chunks['median']} / "
        f"{chunks['maximum']}.",
        f"- Effective overlap min/median/max: {overlaps['minimum']} / "
        f"{overlaps['median']} / {overlaps['maximum']}.",
        f"- Repeated event IDs within a unit: {corpus['units_with_repeated_event_id']}; "
        f"coverage errors: {corpus['coverage_errors']}; range errors: "
        f"{corpus['range_errors']}; planning failures: {corpus['planning_failure_count']}.",
        f"- Deterministic second build equality: "
        f"`{str(corpus['deterministic_second_build_equality']).lower()}`.",
        "",
        "## Previous A/B event sets",
        "",
        "| Set | v3 units | Prompt estimates | Chunks | Complete coverage |",
        "|---|---:|---|---:|---:|",
    ]
    for name, item in data["ab_event_sets"].items():
        if "planning_error" in item:
            lines.append(f"| {name} | error | n/a | n/a | false |")
        else:
            lines.append(
                f"| {name} | {item['resulting_v3_unit_count']} | "
                f"{item['prompt_estimates']} | {item['chunk_count']} | "
                f"{item['complete_event_range_coverage']} |"
            )

    bird = data["ab_event_sets"].get("bird_list", {})
    lines.extend(["", "## Bird-list", ""])
    if "planning_error" in bird:
        lines.append(f"Planning error: {bird['planning_error']}")
    else:
        lines.extend(
            [
                f"- Safe source IDs: {bird['source_event_safe_ids']}",
                f"- Units: {bird['resulting_v3_unit_count']}; chunks: "
                f"{bird['chunk_count']}; maximum estimate: "
                f"{bird['maximum_estimated_prompt_tokens']}.",
                f"- Complete source coverage: "
                f"`{str(bird['complete_event_range_coverage']).lower()}`.",
                f"- Any chunk equals or exceeds 5632: "
                f"`{str(bird['any_chunk_equals_or_exceeds_5632']).lower()}`.",
                "",
                "| Unit | Attempt 1 | Maximum retry | Retry variant | Worst case | Ranges |",
                "|---|---:|---:|---|---:|---|",
            ]
        )
        for unit in bird["unit_prompt_budgets"]:
            range_text = "; ".join(
                f"{item['source_event_safe_id']}:{item['start_char']}-{item['end_char']} "
                f"(overlap {item['overlap_characters']})"
                for item in unit["ranges"]
            )
            lines.append(
                f"| {unit['unit_safe_id']} | {unit['attempt_1_estimate']} | "
                f"{unit['maximum_retry_estimate']} | {unit['maximum_retry_variant']} | "
                f"{unit['worst_case_estimate']} | {range_text} |"
            )
        lines.extend(
            [
                "",
                "| Chunk source | Start | End | Length | Overlap | Attempt 1 | "
                "Maximum retry | Retry variant |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for source_id, source_chunks in bird["chunks_by_oversized_source_event"].items():
            for chunk in source_chunks:
                lines.append(
                    f"| {source_id} | {chunk['start_char']} | {chunk['end_char']} | "
                    f"{chunk['character_length']} | {chunk['overlap_characters']} | "
                    f"{chunk['attempt_1_estimate']} | {chunk['maximum_retry_estimate']} | "
                    f"{chunk['maximum_retry_variant']} |"
                )
    return "\n".join(lines) + "\n"


def write_dry_run_reports(
    *,
    main_db: Path,
    calibration_json: Path,
    comparison_sidecar: Path,
    output_json: Path,
    output_markdown: Path,
) -> dict[str, Any]:
    if output_json.exists() or output_markdown.exists():
        raise FileExistsError("Dry-run report output already exists; refusing to overwrite")
    data = build_dry_run_report(
        main_db=main_db,
        calibration_json=calibration_json,
        comparison_sidecar=comparison_sidecar,
    )
    output_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    generated = json.loads(output_json.read_text(encoding="utf-8"))
    output_markdown.write_text(render_markdown(generated), encoding="utf-8")
    return data
