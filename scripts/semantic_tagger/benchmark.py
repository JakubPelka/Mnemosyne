"""Offline, human-gated benchmark preparation for semantic-tagger v3.

This module has no inference or network client.  It reads existing SQLite files
through immutable, query-only connections and writes payload-bearing artifacts
only below the repository's ignored ``.local`` benchmark directory.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import sqlite3
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from scripts.semantic_tagger.prompt_budget import (
    PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
    DEFAULT_CHUNK_BOUNDARY_BACKTRACK_CHARACTERS,
    DEFAULT_CHUNK_OVERLAP_CHARACTERS,
    DEFAULT_MAX_PROMPT_TOKENS,
    DEFAULT_NUM_CTX,
    DEFAULT_NUM_PREDICT,
    DEFAULT_SAFETY_MARGIN,
    PROMPT_ESTIMATOR_VERSION,
)
from scripts.semantic_tagger.prompt_builder import SUPPORTED_PROMPT_VARIANTS
from scripts.semantic_tagger.qwen3_tokenizer import EXACT_PROMPT_ESTIMATOR_VERSION
from scripts.semantic_tagger.schemas import TaggerOutputV3Stored
from scripts.semantic_tagger.unit_planner import PROMPT_BUDGET_STRATEGY_VERSION
from scripts.semantic_tagger.unit_serializer import (
    compute_reconstructed_content_hash,
    serialize_semantic_unit,
)


SELECTOR_VERSION = "semantic-benchmark-selector-v1"
ANONYMIZER_VERSION = "semantic-benchmark-anonymizer-v1"
REDACTION_REPORT_VERSION = "semantic-benchmark-redaction-report-v1"
PROMPT_VERSION = "semantic-hybrid-v3"
SCHEMA_VERSION = "semantic-tags-v3"
MODEL_NAME = "qwen3:14b"
GENERATION_SEED = 42
CASES_PER_CATEGORY = 2
LOCAL_ARTIFACT_ROOT = Path(".local/semantic_tagger_benchmark")
SAFE_MANIFEST_PATH = LOCAL_ARTIFACT_ROOT / "manifest.local.json"
INVENTORY_PATH = LOCAL_ARTIFACT_ROOT / "inventory/sanitized_inventory.local.json"
INVENTORY_VERSION = "semantic-tagger-sidecar-inventory-v1"
HUMAN_REVIEW_PREVIEW_VERSION = "semantic-tagger-human-review-preview-v1"
COMPATIBLE_REASON_CODE = "compatible_frozen_semantic_hybrid_v3_contract"
TERMINAL_JOB_STATUSES = ("done", "failed")
SUPPORTED_BENCHMARK_ESTIMATORS = frozenset(
    {PROMPT_ESTIMATOR_VERSION, EXACT_PROMPT_ESTIMATOR_VERSION}
)

CATEGORY_PRIORITY = (
    "relation_rich",
    "chunked",
    "near_prompt_limit",
    "historically_empty_or_rejected",
    "problematic_or_generic_labels",
    "ordinary_control",
)

GENERIC_LABEL_VERSION = "generic-labels-v1"
GENERIC_LABELS = frozenset(
    {
        "data",
        "dane",
        "informacja",
        "informacje",
        "information",
        "inne",
        "inny",
        "issue",
        "losning",
        "okand",
        "other",
        "problem",
        "process",
        "proces",
        "project",
        "projekt",
        "praca",
        "rozwiazanie",
        "solution",
        "system",
        "task",
        "uppgift",
        "unknown",
        "work",
        "arbete",
        "annat",
        "nieznane",
        "zadanie",
    }
)


class BenchmarkError(ValueError):
    """A deterministic benchmark preparation failure."""


class BenchmarkQuotaError(BenchmarkError):
    """Raised when an exact two-case category quota cannot be met."""


@dataclass(frozen=True)
class CandidateDiagnostics:
    relation_count: int
    relation_evidence_count: int
    planned_chunk_count: int
    estimated_prompt_tokens: int
    prompt_limit: int
    historically_empty: bool
    historically_rejected: bool
    generic_label_count: int
    duplicate_label_count: int
    accepted: bool


@dataclass(frozen=True)
class BenchmarkCandidate:
    """Local-only candidate metadata; source identifiers never enter a manifest."""

    source_unit_id: str
    source_content_hash: str
    source_context_id: str
    opaque_hash: str
    provenance_hash: str
    diagnostics: CandidateDiagnostics


@dataclass(frozen=True)
class SelectedCase:
    candidate: BenchmarkCandidate
    case_id: str
    category: str
    reason_codes: tuple[str, ...]
    overlap_reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RunContract:
    run_id: str
    prompt_version: str
    schema_version: str
    unit_strategy_version: str
    model_name: str
    settings: dict[str, Any]
    source_sidecar_provenance_hash: str


@dataclass(frozen=True)
class AnonymizationResult:
    anonymized: Any
    reversible_mapping: dict[str, str]
    replacement_counts: dict[str, int]
    unresolved_reason_codes: tuple[str, ...]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise BenchmarkError("Unable to hash a benchmark input file") from None
    return digest.hexdigest()


def opaque_hash(secret_salt: bytes, *parts: str) -> str:
    if len(secret_salt) < 16:
        raise BenchmarkError("Benchmark salt must contain at least 16 bytes")
    message = "\x1f".join(parts).encode("utf-8")
    return hmac.new(secret_salt, message, hashlib.sha256).hexdigest()


def sidecar_source_id(sidecar_path: Path, secret_salt: bytes) -> str:
    """Return the existing salted provenance identifier for one sidecar."""
    return opaque_hash(secret_salt, "sidecar", file_sha256(sidecar_path))


def opaque_run_id(secret_salt: bytes, source_id: str, run_id: str) -> str:
    return opaque_hash(secret_salt, "run", source_id, run_id)


def _frozen_runtime_settings() -> dict[str, Any]:
    return {
        "num_ctx": DEFAULT_NUM_CTX,
        "max_prompt_tokens": DEFAULT_MAX_PROMPT_TOKENS,
        "num_predict": DEFAULT_NUM_PREDICT,
        "safety_margin": DEFAULT_SAFETY_MARGIN,
        "think": False,
        "stream": False,
        "temperature": 0,
        "seed": GENERATION_SEED,
        "chunk_overlap_characters": DEFAULT_CHUNK_OVERLAP_CHARACTERS,
        "chunk_boundary_backtrack_characters": (DEFAULT_CHUNK_BOUNDARY_BACKTRACK_CHARACTERS),
    }


def _parse_run_settings(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    try:
        settings = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return None, "run_settings_invalid_json"
    if not isinstance(settings, dict):
        return None, "run_settings_not_object"
    return settings, None


def _run_contract_reason_codes(
    row: Mapping[str, Any],
    settings: Mapping[str, Any] | None,
    settings_error: str | None,
    *,
    total_jobs: int,
    terminal_jobs: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    expected_fields = (
        ("prompt_version", PROMPT_VERSION, "prompt_version_mismatch"),
        ("schema_version", SCHEMA_VERSION, "schema_version_mismatch"),
        (
            "unit_strategy_version",
            PROMPT_BUDGET_STRATEGY_VERSION,
            "planner_strategy_mismatch",
        ),
        ("model_name", MODEL_NAME, "model_name_mismatch"),
    )
    for field, expected, reason in expected_fields:
        if row[field] != expected:
            reasons.append(reason)
    if settings_error:
        reasons.append(settings_error)
    elif settings is not None:
        for field, expected in _frozen_runtime_settings().items():
            if settings.get(field) != expected:
                reasons.append(f"runtime_setting_{field}_mismatch")
        estimator = settings.get("prompt_estimator_version")
        if not isinstance(estimator, str) or not estimator:
            reasons.append("prompt_estimator_version_missing")
        elif estimator not in SUPPORTED_BENCHMARK_ESTIMATORS:
            reasons.append("prompt_estimator_version_unsupported")
        estimator_contract = settings.get("prompt_estimator_contract")
        if estimator == PROMPT_ESTIMATOR_VERSION and estimator_contract is not None:
            reasons.append("prompt_estimator_contract_unexpected")
        if estimator == EXACT_PROMPT_ESTIMATOR_VERSION and not isinstance(estimator_contract, dict):
            reasons.append("prompt_estimator_contract_missing")
    if total_jobs == 0:
        reasons.append("run_has_no_jobs")
    elif terminal_jobs != total_jobs:
        reasons.append("run_has_nonterminal_jobs")
    return tuple(reasons or (COMPATIBLE_REASON_CODE,))


def _safe_metadata_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 160:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+-]*", value):
        return None
    return value


def _safe_repository_filename(repo_root: Path, resolved: Path) -> str | None:
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return None
    if not re.fullmatch(r"semantic_tagger[A-Za-z0-9_.-]*\.local\.sqlite3", resolved.name):
        return None
    return resolved.name


def _companion_states(path: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    for label, suffix in (("wal", "-wal"), ("shm", "-shm"), ("journal", "-journal")):
        companion = Path(f"{path}{suffix}")
        try:
            metadata = companion.lstat()
        except FileNotFoundError:
            states[label] = "absent"
        except OSError:
            states[label] = "unavailable"
        else:
            if companion.is_symlink() or not companion.is_file():
                states[label] = "not_regular"
            elif metadata.st_size == 0:
                states[label] = "empty"
            else:
                states[label] = "nonzero"
    return states


def _matching_open_handle_exists(paths: Sequence[Path]) -> bool | None:
    """Use a silent inode probe; never return process IDs or command output."""
    existing = []
    for path in paths:
        if path.exists():
            existing.append(str(path))
    if not existing:
        return False
    try:
        result = subprocess.run(
            ["fuser", "-s", "-I", *existing],
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _runtime_preflight_reason_codes(path: Path) -> tuple[str, ...]:
    companion_states = _companion_states(path)
    reasons: list[str] = []
    if companion_states["wal"] == "nonzero":
        reasons.append("unsafe_nonzero_wal")
    if companion_states["journal"] != "absent":
        reasons.append("unsafe_rollback_journal_state")
    if any(state in {"not_regular", "unavailable"} for state in companion_states.values()):
        reasons.append("unsafe_companion_state")
    handle_paths = [path, *(Path(f"{path}{suffix}") for suffix in ("-wal", "-shm"))]
    active_handle = _matching_open_handle_exists(handle_paths)
    if active_handle is True:
        reasons.append("unsafe_active_open_handle")
    elif active_handle is None:
        reasons.append("unsafe_open_handle_check_unavailable")
    return tuple(reasons)


def make_candidate(
    *,
    secret_salt: bytes,
    source_unit_id: str,
    source_content_hash: str,
    source_context_id: str,
    diagnostics: CandidateDiagnostics,
) -> BenchmarkCandidate:
    return BenchmarkCandidate(
        source_unit_id=source_unit_id,
        source_content_hash=source_content_hash,
        source_context_id=source_context_id,
        opaque_hash=opaque_hash(secret_salt, "unit", source_unit_id, source_content_hash),
        provenance_hash=opaque_hash(secret_salt, "provenance", source_content_hash),
        diagnostics=diagnostics,
    )


def _nearest_rank(values: Sequence[int], percentile: float) -> int:
    ordered = sorted(values)
    if not ordered:
        raise BenchmarkQuotaError("No candidates are available for percentile diagnostics")
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _qualification_reason_codes(
    candidate: BenchmarkCandidate,
    ordinary_lower: int,
    ordinary_upper: int,
) -> dict[str, str]:
    diagnostics = candidate.diagnostics
    qualifications: dict[str, str] = {}
    if diagnostics.relation_count > 0 and diagnostics.relation_evidence_count > 0:
        qualifications["relation_rich"] = "has_relations_with_evidence_diagnostics"
    if diagnostics.planned_chunk_count > 1:
        qualifications["chunked"] = "planner_predicted_multiple_chunks"
    near_limit = (
        diagnostics.planned_chunk_count == 1
        and diagnostics.estimated_prompt_tokens <= diagnostics.prompt_limit
        and diagnostics.estimated_prompt_tokens * 10 >= diagnostics.prompt_limit * 9
    )
    if near_limit:
        qualifications["near_prompt_limit"] = "unchunked_prompt_within_90_to_100_percent"
    if diagnostics.historically_empty or diagnostics.historically_rejected:
        qualifications["historically_empty_or_rejected"] = (
            "historical_output_empty_or_validation_rejected"
        )
    if diagnostics.generic_label_count > 0 or diagnostics.duplicate_label_count > 0:
        qualifications["problematic_or_generic_labels"] = "historical_generic_or_duplicate_labels"
    if (
        diagnostics.accepted
        and diagnostics.planned_chunk_count == 1
        and ordinary_lower <= diagnostics.estimated_prompt_tokens <= ordinary_upper
        and not qualifications
    ):
        qualifications["ordinary_control"] = "accepted_unchunked_interquartile_control"
    return qualifications


def _category_sort_key(category: str, candidate: BenchmarkCandidate) -> tuple[Any, ...]:
    diagnostics = candidate.diagnostics
    primary: dict[str, tuple[int, ...]] = {
        "relation_rich": (
            diagnostics.relation_evidence_count,
            diagnostics.relation_count,
        ),
        "chunked": (diagnostics.planned_chunk_count, diagnostics.estimated_prompt_tokens),
        "near_prompt_limit": (diagnostics.estimated_prompt_tokens,),
        "historically_empty_or_rejected": (
            int(diagnostics.historically_rejected),
            int(diagnostics.historically_empty),
        ),
        "problematic_or_generic_labels": (
            diagnostics.generic_label_count + diagnostics.duplicate_label_count,
            diagnostics.generic_label_count,
            diagnostics.duplicate_label_count,
        ),
        "ordinary_control": (diagnostics.estimated_prompt_tokens,),
    }[category]
    return (*(-value for value in primary), candidate.opaque_hash)


def select_benchmark_cases(
    candidates: Sequence[BenchmarkCandidate],
) -> tuple[list[SelectedCase], dict[str, int]]:
    """Select exactly two mutually exclusive cases for all six categories."""
    if len({candidate.opaque_hash for candidate in candidates}) != len(candidates):
        raise BenchmarkError("Candidate opaque hashes must be unique")
    prompt_sizes = [candidate.diagnostics.estimated_prompt_tokens for candidate in candidates]
    ordinary_lower = _nearest_rank(prompt_sizes, 0.25)
    ordinary_upper = _nearest_rank(prompt_sizes, 0.75)
    qualifications = {
        candidate.opaque_hash: _qualification_reason_codes(
            candidate,
            ordinary_lower,
            ordinary_upper,
        )
        for candidate in candidates
    }

    selected: list[SelectedCase] = []
    used: set[str] = set()
    for category in CATEGORY_PRIORITY:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.opaque_hash not in used
            and category in qualifications[candidate.opaque_hash]
        ]
        eligible.sort(key=lambda candidate: _category_sort_key(category, candidate))
        if len(eligible) < CASES_PER_CATEGORY:
            raise BenchmarkQuotaError(
                f"Category {category} has {len(eligible)} eligible cases; "
                f"exactly {CASES_PER_CATEGORY} are required"
            )
        for candidate in eligible[:CASES_PER_CATEGORY]:
            all_reasons = qualifications[candidate.opaque_hash]
            overlaps = tuple(
                all_reasons[other]
                for other in CATEGORY_PRIORITY
                if other != category and other in all_reasons
            )
            selected.append(
                SelectedCase(
                    candidate=candidate,
                    case_id=f"case-{candidate.opaque_hash[:20]}",
                    category=category,
                    reason_codes=(all_reasons[category],),
                    overlap_reason_codes=overlaps,
                )
            )
            used.add(candidate.opaque_hash)

    return selected, {
        "ordinary_prompt_p25": ordinary_lower,
        "ordinary_prompt_p75": ordinary_upper,
    }


def selector_configuration_hash(contract: RunContract, percentile_bounds: Mapping[str, int]) -> str:
    return sha256_json(
        {
            "selector_version": SELECTOR_VERSION,
            "category_priority": CATEGORY_PRIORITY,
            "cases_per_category": CASES_PER_CATEGORY,
            "near_limit_minimum_percent": 90,
            "ordinary_percentiles": [25, 75],
            "generic_label_version": GENERIC_LABEL_VERSION,
            "generic_labels": sorted(GENERIC_LABELS),
            "prompt_version": contract.prompt_version,
            "schema_version": contract.schema_version,
            "unit_strategy_version": contract.unit_strategy_version,
            "model_name": contract.model_name,
            "settings": contract.settings,
            "percentile_bounds": dict(percentile_bounds),
        }
    )


def build_safe_manifest(
    selected: Sequence[SelectedCase],
    contract: RunContract,
    percentile_bounds: Mapping[str, int],
) -> dict[str, Any]:
    config_hash = selector_configuration_hash(contract, percentile_bounds)
    cases = []
    for item in selected:
        diagnostics = asdict(item.candidate.diagnostics)
        cases.append(
            {
                "case_id": item.case_id,
                "opaque_unit_hash": item.candidate.opaque_hash,
                "source_provenance_hash": item.candidate.provenance_hash,
                "category": item.category,
                "diagnostics": diagnostics,
                "reason_codes": list(item.reason_codes),
                "overlap_reason_codes": list(item.overlap_reason_codes),
            }
        )
    return {
        "manifest_version": "semantic-tagger-benchmark-manifest-v1",
        "selector_version": SELECTOR_VERSION,
        "selector_configuration_hash": config_hash,
        "source_sidecar_provenance_hash": contract.source_sidecar_provenance_hash,
        "prompt_version": contract.prompt_version,
        "schema_version": contract.schema_version,
        "unit_strategy_version": contract.unit_strategy_version,
        "case_count": len(cases),
        "cases": cases,
    }


def _resolved_sqlite_path(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise BenchmarkError("SQLite input must be an existing regular file") from None
    if not resolved.is_file():
        raise BenchmarkError("SQLite input must be an existing regular file")
    return resolved


def _sqlite_uri(path: Path) -> str:
    resolved = _resolved_sqlite_path(path)
    if _runtime_preflight_reason_codes(resolved):
        raise BenchmarkError("SQLite input has an unsafe or ambiguous runtime state")
    return f"{resolved.as_uri()}?mode=ro&immutable=1"


def _database_snapshot(path: Path) -> str:
    _sqlite_uri(path)
    return file_sha256(_resolved_sqlite_path(path))


def open_sqlite_immutable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_sqlite_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise BenchmarkError("SQLite benchmark connection is not query-only")
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def _inventory_run_reports(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    secret_salt: bytes,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    run_columns = _table_columns(connection, "tagging_run")
    job_columns = _table_columns(connection, "tagging_job")
    required_run_columns = {
        "run_id",
        "prompt_version",
        "schema_version",
        "unit_strategy_version",
        "model_name",
        "settings_json",
    }
    if not required_run_columns <= run_columns or not {"run_id", "status"} <= job_columns:
        return [], ("unsupported_sidecar_schema",)

    optional_columns = [
        column for column in ("model_digest", "ollama_version") if column in run_columns
    ]
    selected_columns = sorted(required_run_columns) + optional_columns
    rows = connection.execute(
        f"SELECT {', '.join(selected_columns)} FROM tagging_run ORDER BY run_id"
    ).fetchall()
    reports: list[dict[str, Any]] = []
    for row in rows:
        counts = connection.execute(
            """
            SELECT COUNT(*) AS total_jobs,
                   SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_jobs,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs
            FROM tagging_job WHERE run_id = ?
            """,
            (row["run_id"],),
        ).fetchone()
        total_jobs = int(counts["total_jobs"] or 0)
        terminal_counts = {
            "done": int(counts["done_jobs"] or 0),
            "failed": int(counts["failed_jobs"] or 0),
        }
        terminal_jobs = sum(terminal_counts.values())
        settings, settings_error = _parse_run_settings(row["settings_json"])
        reason_codes = _run_contract_reason_codes(
            row,
            settings,
            settings_error,
            total_jobs=total_jobs,
            terminal_jobs=terminal_jobs,
        )
        estimator = settings.get("prompt_estimator_version") if settings else None
        estimator_contract = settings.get("prompt_estimator_contract") if settings else None
        contract_id = (
            estimator_contract.get("pin_set_id") if isinstance(estimator_contract, dict) else None
        )
        model_metadata = {
            "name": _safe_metadata_identifier(row["model_name"]),
            "digest": (
                _safe_metadata_identifier(row["model_digest"])
                if "model_digest" in run_columns
                else None
            ),
            "runtime_version": (
                _safe_metadata_identifier(row["ollama_version"])
                if "ollama_version" in run_columns
                else None
            ),
        }
        reports.append(
            {
                "opaque_run_id": opaque_run_id(secret_salt, source_id, str(row["run_id"])),
                "prompt_version": _safe_metadata_identifier(row["prompt_version"]),
                "schema_version": _safe_metadata_identifier(row["schema_version"]),
                "planner_strategy": _safe_metadata_identifier(row["unit_strategy_version"]),
                "prompt_estimator": {
                    "version": _safe_metadata_identifier(estimator),
                    "contract_id": _safe_metadata_identifier(contract_id),
                },
                "model_metadata": model_metadata,
                "terminal_job_counts": terminal_counts,
                "compatibility_reason_codes": list(reason_codes),
                "compatible": reason_codes == (COMPATIBLE_REASON_CODE,),
            }
        )
    reports.sort(key=lambda item: item["opaque_run_id"])
    return reports, ()


def _inventory_sidecar(
    repo_root: Path,
    sidecar_path: Path,
    secret_salt: bytes,
) -> dict[str, Any]:
    try:
        if sidecar_path.is_symlink():
            raise OSError
        resolved = sidecar_path.resolve(strict=True)
        if not resolved.is_file():
            raise OSError
    except OSError:
        raise BenchmarkError("Inventory source must be an existing regular sidecar") from None
    if not re.fullmatch(r"semantic_tagger[A-Za-z0-9_.-]*\.local\.sqlite3", resolved.name):
        raise BenchmarkError("Inventory source name is outside the semantic-tagger allowlist")

    source_hash = file_sha256(resolved)
    source_id = opaque_hash(secret_salt, "sidecar", source_hash)
    initial_companions = _companion_states(resolved)
    report: dict[str, Any] = {
        "source_id": source_id,
        "file_sha256": source_hash,
        "sqlite_integrity": "not_checked",
        "companion_files": initial_companions,
        "sidecar_schema_version": None,
        "source_status": "rejected",
        "source_reason_codes": [],
        "run_count": None,
        "runs": [],
    }
    safe_filename = _safe_repository_filename(repo_root, resolved)
    if safe_filename:
        report["source_filename"] = safe_filename

    preflight_reasons = _runtime_preflight_reason_codes(resolved)
    if preflight_reasons:
        report["source_reason_codes"] = list(preflight_reasons)
        return report

    connection: sqlite3.Connection | None = None
    integrity_completed = False
    try:
        connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            report["source_reason_codes"] = ["query_only_mode_unavailable"]
            return report
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        if len(integrity_rows) != 1 or integrity_rows[0][0] != "ok":
            report["sqlite_integrity"] = "failed"
            report["source_reason_codes"] = ["sqlite_integrity_failed"]
            return report
        report["sqlite_integrity"] = "ok"
        integrity_completed = True

        meta_columns = _table_columns(connection, "sidecar_meta")
        if {"key", "value"} <= meta_columns:
            meta_row = connection.execute(
                "SELECT value FROM sidecar_meta WHERE key = 'schema_version'"
            ).fetchone()
            if meta_row:
                report["sidecar_schema_version"] = _safe_metadata_identifier(str(meta_row["value"]))

        run_reports, schema_reasons = _inventory_run_reports(
            connection,
            source_id=source_id,
            secret_salt=secret_salt,
        )
        report["runs"] = run_reports
        report["run_count"] = len(run_reports)
        if schema_reasons:
            report["source_reason_codes"] = list(schema_reasons)
            return report

        active_runtime = False
        worker_columns = _table_columns(connection, "worker_state")
        if "status" in worker_columns:
            active_runtime = bool(
                connection.execute(
                    "SELECT 1 FROM worker_state WHERE status = 'running' LIMIT 1"
                ).fetchone()
            )
        if connection.execute(
            "SELECT 1 FROM tagging_job WHERE status = 'running' LIMIT 1"
        ).fetchone():
            active_runtime = True
        if active_runtime:
            report["source_reason_codes"] = ["unsafe_ambiguous_runtime_state"]
            for run_report in report["runs"]:
                run_report["compatible"] = False
                run_report["compatibility_reason_codes"] = ["source_runtime_state_unsafe"]
            return report
        report["source_status"] = "accepted"
        report["source_reason_codes"] = ["safe_metadata_only_source"]
    except sqlite3.Error:
        if integrity_completed:
            report["source_reason_codes"] = ["sqlite_metadata_inspection_failed"]
        else:
            report["source_reason_codes"] = ["sqlite_integrity_failed"]
            report["sqlite_integrity"] = "failed"
        report["run_count"] = None
        report["runs"] = []
        return report
    finally:
        if connection is not None:
            connection.close()

    final_companions = _companion_states(resolved)
    final_hash = file_sha256(resolved)
    final_handle_state = _matching_open_handle_exists(
        [resolved, Path(f"{resolved}-wal"), Path(f"{resolved}-shm")]
    )
    if (
        final_hash != source_hash
        or final_companions != initial_companions
        or final_handle_state is not False
    ):
        report["source_status"] = "rejected"
        report["source_reason_codes"] = ["unsafe_runtime_state_changed"]
        for run_report in report["runs"]:
            run_report["compatible"] = False
            run_report["compatibility_reason_codes"] = ["source_runtime_state_unsafe"]
    return report


def inventory_sidecars(
    *,
    repo_root: Path,
    sidecar_allowlist: Sequence[Path],
    secret_salt: bytes,
) -> dict[str, Any]:
    """Inspect only bounded sidecar metadata from an explicit allowlist."""
    try:
        root = repo_root.resolve(strict=True)
    except OSError:
        raise BenchmarkError("Repository root must be an existing directory") from None
    if not sidecar_allowlist:
        raise BenchmarkError("At least one explicit sidecar is required")
    resolved_keys: set[str] = set()
    reports = []
    for requested in sidecar_allowlist:
        candidate = requested if requested.is_absolute() else root / requested
        try:
            key = str(candidate.resolve(strict=True))
        except OSError:
            raise BenchmarkError("Inventory source must be an existing regular sidecar") from None
        if key in resolved_keys:
            raise BenchmarkError("Inventory sidecar allowlist contains a duplicate")
        resolved_keys.add(key)
        reports.append(_inventory_sidecar(root, candidate, secret_salt))
    reports.sort(key=lambda item: item["source_id"])
    compatible_runs = [
        run
        for source in reports
        if source["source_status"] == "accepted"
        for run in source["runs"]
        if run["compatible"]
    ]
    return {
        "inventory_version": INVENTORY_VERSION,
        "frozen_contract": {
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "planner_strategy": PROMPT_BUDGET_STRATEGY_VERSION,
        },
        "sidecar_count": len(reports),
        "unsafe_sidecar_count": sum(source["source_status"] == "rejected" for source in reports),
        "compatible_run_count": len(compatible_runs),
        "sources": reports,
    }


def write_sidecar_inventory(
    *,
    repo_root: Path,
    sidecar_allowlist: Sequence[Path],
    secret_salt: bytes,
    output_path: Path = INVENTORY_PATH,
) -> dict[str, Any]:
    root = repo_root.resolve(strict=True)
    expected = (root / INVENTORY_PATH).resolve(strict=False)
    requested = output_path if output_path.is_absolute() else root / output_path
    requested = requested.resolve(strict=False)
    if requested != expected:
        raise BenchmarkError("The sanitized inventory path is fixed by the contract")
    verify_ignored_local_output(root, requested)
    report = inventory_sidecars(
        repo_root=root,
        sidecar_allowlist=sidecar_allowlist,
        secret_salt=secret_salt,
    )
    _write_new_json(requested, report)
    return report


def _validated_run_contract(
    connection: sqlite3.Connection,
    sidecar_path: Path,
    secret_salt: bytes,
    run_id: str,
) -> RunContract:
    row = connection.execute("SELECT * FROM tagging_run WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise BenchmarkError("Requested semantic-tagger run was not found")
    settings, settings_error = _parse_run_settings(row["settings_json"])
    counts = connection.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status IN ('done', 'failed') THEN 1 ELSE 0 END) AS terminal "
        "FROM tagging_job WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    reason_codes = _run_contract_reason_codes(
        row,
        settings,
        settings_error,
        total_jobs=int(counts["total"] or 0),
        terminal_jobs=int(counts["terminal"] or 0),
    )
    if reason_codes != (COMPATIBLE_REASON_CODE,) or settings is None:
        raise BenchmarkError("Approved run is incompatible with the frozen v3 contract")
    return RunContract(
        run_id=row["run_id"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        unit_strategy_version=row["unit_strategy_version"],
        model_name=row["model_name"],
        settings=settings,
        source_sidecar_provenance_hash=sidecar_source_id(sidecar_path, secret_salt),
    )


def _resolve_approved_raw_run_id(
    connection: sqlite3.Connection,
    *,
    secret_salt: bytes,
    source_id: str,
    approved_run_id: str,
) -> str:
    matches = [
        str(row["run_id"])
        for row in connection.execute("SELECT run_id FROM tagging_run")
        if hmac.compare_digest(
            opaque_run_id(secret_salt, source_id, str(row["run_id"])),
            approved_run_id,
        )
    ]
    if len(matches) != 1:
        raise BenchmarkError("Approved opaque run ID was not found in the approved sidecar")
    return matches[0]


def _normalized_label(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_")


_VALIDATION_REJECTION_CODES = frozenset({"facets_missing", "invalid_json", "validation_error"})


def _historical_output_diagnostics(row: sqlite3.Row) -> dict[str, Any]:
    output: dict[str, Any] = {}
    if row["output_json"]:
        try:
            parsed = json.loads(row["output_json"])
            if isinstance(parsed, dict):
                output = parsed
        except (TypeError, json.JSONDecodeError):
            if row["status"] == "done":
                raise BenchmarkError("Completed historical output is not valid JSON") from None
    if row["status"] == "done":
        try:
            output = TaggerOutputV3Stored.model_validate(output).model_dump()
        except Exception:
            raise BenchmarkError("Completed historical output violates TaggerOutputV3") from None
    concepts = output.get("concepts") if isinstance(output.get("concepts"), list) else []
    relations = output.get("relations") if isinstance(output.get("relations"), list) else []
    relation_evidence_count = sum(
        len(relation.get("evidence_event_ids", relation.get("evidence", [])))
        for relation in relations
        if isinstance(relation, dict)
        and isinstance(relation.get("evidence_event_ids", relation.get("evidence", [])), list)
    )
    labels = [
        _normalized_label(str(concept.get("preferred_label") or concept.get("surface_label") or ""))
        for concept in concepts
        if isinstance(concept, dict)
    ]
    labels = [label for label in labels if label]
    counts = Counter(labels)
    return {
        "relation_count": len(relations),
        "relation_evidence_count": relation_evidence_count,
        "historically_empty": row["status"] == "done" and not concepts,
        "historically_rejected": (
            row["status"] == "failed" and row["error_code"] in _VALIDATION_REJECTION_CODES
        ),
        "generic_label_count": sum(label in GENERIC_LABELS for label in labels),
        "duplicate_label_count": sum(count - 1 for count in counts.values() if count > 1),
        "accepted": row["status"] == "done" and bool(concepts),
    }


def load_benchmark_candidates(
    sidecar_path: Path,
    secret_salt: bytes,
    *,
    approved_source_id: str,
    approved_run_id: str,
) -> tuple[RunContract, list[BenchmarkCandidate]]:
    """Load only numeric/historical diagnostics from a frozen v3 sidecar."""
    actual_source_id = sidecar_source_id(sidecar_path, secret_salt)
    if not hmac.compare_digest(actual_source_id, approved_source_id):
        raise BenchmarkError("Explicitly approved sidecar ID does not match the source")
    with contextlib.closing(open_sqlite_immutable(sidecar_path)) as connection:
        raw_run_id = _resolve_approved_raw_run_id(
            connection,
            secret_salt=secret_salt,
            source_id=actual_source_id,
            approved_run_id=approved_run_id,
        )
        contract = _validated_run_contract(connection, sidecar_path, secret_salt, raw_run_id)
        rows = connection.execute(
            """
            SELECT u.*, j.status, j.output_json, j.error_code
            FROM tagging_unit AS u
            JOIN tagging_job AS j ON j.unit_id = u.unit_id
            WHERE j.run_id = ?
            ORDER BY u.unit_id
            """,
            (contract.run_id,),
        ).fetchall()

    parsed_manifests: dict[str, dict[str, Any]] = {}
    chunk_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        try:
            manifest = json.loads(row["segments_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            raise BenchmarkError("Stored planner manifest is not valid JSON") from None
        if not isinstance(manifest, dict):
            raise BenchmarkError("Stored planner manifest must be a JSON object")
        if manifest.get("unit_strategy_version") != contract.unit_strategy_version:
            raise BenchmarkError("Stored planner strategy diagnostic does not match the run")
        if manifest.get("prompt_version") != contract.prompt_version:
            raise BenchmarkError("Stored planner prompt diagnostic does not match the run")
        if manifest.get("schema_version") != contract.schema_version:
            raise BenchmarkError("Stored planner schema diagnostic does not match the run")
        if manifest.get("prompt_budget_basis") != PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS:
            raise BenchmarkError("Stored unit lacks the authoritative prompt-budget basis")
        if (
            manifest.get("prompt_estimator_version")
            != contract.settings["prompt_estimator_version"]
        ):
            raise BenchmarkError("Stored unit prompt estimator does not match the run")
        for field in (
            "prompt_estimator_contract",
            "chunk_overlap_characters",
            "chunk_boundary_backtrack_characters",
        ):
            if manifest.get(field) != contract.settings.get(field):
                raise BenchmarkError("Stored planner settings do not match the frozen run")
        prompt_budget = manifest.get("prompt_budget")
        if not isinstance(prompt_budget, dict):
            raise BenchmarkError("Stored unit lacks planner prompt-budget diagnostics")
        stored_estimate = row["estimated_token_count"]
        variant_estimates = prompt_budget.get("variant_estimates")
        variant_names = [variant.name for variant in SUPPORTED_PROMPT_VARIANTS]
        if (
            not isinstance(variant_estimates, dict)
            or list(variant_estimates) != variant_names
            or any(
                not isinstance(variant_estimates[name], int) or variant_estimates[name] < 1
                for name in variant_names
            )
        ):
            raise BenchmarkError("Stored prompt variants are not authoritative planner diagnostics")
        retry_names = variant_names[1:]
        expected_retry_variant = max(retry_names, key=lambda name: variant_estimates[name])
        expected_worst_variant = max(variant_names, key=lambda name: variant_estimates[name])
        if (
            not isinstance(stored_estimate, int)
            or stored_estimate < 1
            or prompt_budget.get("worst_case_prompt_estimate") != stored_estimate
            or prompt_budget.get("initial_prompt_estimate") != variant_estimates[variant_names[0]]
            or prompt_budget.get("maximum_retry_prompt_estimate")
            != variant_estimates[expected_retry_variant]
            or prompt_budget.get("maximum_retry_variant") != expected_retry_variant
            or prompt_budget.get("worst_case_prompt_variant") != expected_worst_variant
            or stored_estimate != variant_estimates[expected_worst_variant]
        ):
            raise BenchmarkError("Stored prompt estimate is not the authoritative planner value")
        segments = manifest.get("segments")
        if (
            not isinstance(segments, list)
            or not segments
            or not all(isinstance(segment, dict) for segment in segments)
        ):
            raise BenchmarkError("Stored planner manifest has no valid segments")
        contains_chunk = any(segment.get("is_chunk") is True for segment in segments)
        if manifest.get("oversized_single_event") is not contains_chunk:
            raise BenchmarkError("Stored chunk diagnostics are internally inconsistent")
        parsed_manifests[row["unit_id"]] = manifest
        for segment in manifest.get("segments", []):
            if segment.get("is_chunk"):
                chunk_groups[(row["context_id"], segment["event_id"])].add(row["unit_id"])

    candidates = []
    prompt_limit = int(contract.settings.get("max_prompt_tokens", DEFAULT_MAX_PROMPT_TOKENS))
    for row in rows:
        historical = _historical_output_diagnostics(row)
        manifest = parsed_manifests[row["unit_id"]]
        planned_chunk_count = 1
        for segment in manifest.get("segments", []):
            if segment.get("is_chunk"):
                planned_chunk_count = max(
                    planned_chunk_count,
                    len(chunk_groups[(row["context_id"], segment["event_id"])]),
                )
        diagnostics = CandidateDiagnostics(
            relation_count=historical["relation_count"],
            relation_evidence_count=historical["relation_evidence_count"],
            planned_chunk_count=planned_chunk_count,
            estimated_prompt_tokens=int(row["estimated_token_count"] or 0),
            prompt_limit=prompt_limit,
            historically_empty=historical["historically_empty"],
            historically_rejected=historical["historically_rejected"],
            generic_label_count=historical["generic_label_count"],
            duplicate_label_count=historical["duplicate_label_count"],
            accepted=historical["accepted"],
        )
        candidates.append(
            make_candidate(
                secret_salt=secret_salt,
                source_unit_id=row["unit_id"],
                source_content_hash=row["content_hash"],
                source_context_id=row["context_id"],
                diagnostics=diagnostics,
            )
        )
    return contract, candidates


_FIELD_CATEGORY_PARTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("EMAIL", ("email", "e_mail")),
    ("PHONE", ("phone", "telephone", "mobile")),
    ("URL", ("url", "uri", "website")),
    ("PATH", ("path", "directory", "folder")),
    ("ADDRESS", ("address", "street", "postal", "postcode", "zip_code")),
    ("PLACE", ("place", "location", "city", "locality")),
    (
        "ORG",
        (
            "organization",
            "organisation",
            "organization_name",
            "organisation_name",
            "company",
            "company_name",
            "employer",
        ),
    ),
    ("PERSON", ("person", "person_name", "full_name", "speaker_name", "author_name")),
    (
        "ACCOUNT",
        ("account", "account_id", "user_id", "device_id", "username", "handle"),
    ),
    ("IP", ("ip_address",)),
)

_PRESERVED_FIELDS = frozenset(
    {
        "case_id",
        "evidence_id",
        "role",
        "prompt_version",
        "schema_version",
        "unit_strategy_version",
    }
)


@dataclass(frozen=True)
class _Detector:
    category: str
    pattern: re.Pattern[str]
    group: int = 0
    validator: Callable[[str], bool] | None = None


_FLAGS = re.IGNORECASE | re.UNICODE
_CAPITALIZED_WORD = r"[A-ZÀ-ÖØ-ÞĄĆĘŁŃÓŚŹŻ][\w'’-]*"


def _valid_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


_DETECTORS = (
    _Detector(
        "URL",
        re.compile(
            r"(?i:\b(?:https?|ftp)://[^\s<>'\"]+|\bwww\.[^\s<>'\"]+|"
            r"\bmailto:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})"
        ),
    ),
    _Detector("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)")),
    _Detector(
        "PATH",
        re.compile(
            r"(?<![\w:])/(?:home|Users)/[^\r\n<>'\"]+?\.[A-Za-z0-9]{1,12}\b|"
            r"(?<![\w:])(?:/[^/\s<>'\"]+){2,}|"
            r"\b[A-Za-z]:\\[^\r\n<>'\"]+?\.[A-Za-z0-9]{1,12}\b"
        ),
    ),
    _Detector(
        "IP",
        re.compile(
            r"(?<![\w:])[0-9A-Fa-f]*:[0-9A-Fa-f:]+(?![\w:])|"
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
        ),
        validator=_valid_ip_address,
    ),
    _Detector(
        "ADDRESS",
        re.compile(
            r"\b\d{1,5}\s+[\wÀ-ž.-]+(?:\s+[\wÀ-ž.-]+){0,4}\s+"
            r"(?:street|st\.?|road|rd\.?|avenue|ave\.?|gata|gatan|väg|vägen|ulica|ul\.?)\b"
            r"(?:\s*,?\s*[A-Z0-9 -]{3,10})?",
            _FLAGS,
        ),
    ),
    _Detector(
        "ADDRESS",
        re.compile(
            r"\b(?:ulica|ul\.?|aleja|al\.?|osiedle|os\.?)\s+"
            r"[\wÀ-ž.-]+(?:\s+[\wÀ-ž.-]+){0,3}\s+\d{1,5}[A-Za-z]?"
            r"(?:[/-]\d{1,5})?(?:\s*,?\s*\d{2}-\d{3}\s+[\wÀ-ž.-]+)?",
            _FLAGS,
        ),
    ),
    _Detector(
        "ADDRESS",
        re.compile(
            r"\b[\wÀ-ž'’-]*(?:gatan|vägen|väg|gränd|allé)\s+\d{1,5}[A-Za-z]?"
            r"(?:\s*,?\s*\d{3}\s?\d{2}\s+[\wÀ-ž'’-]+)?",
            _FLAGS,
        ),
    ),
    _Detector(
        "DATE",
        re.compile(r"\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b"),
    ),
    _Detector(
        "ACCOUNT",
        re.compile(
            r"\b(?:account|user|device|konto|użytkownik)\s*"
            r"(?:id|identifier|nr|number)?\s*[:=#-]\s*([\w-]{4,})",
            _FLAGS,
        ),
        1,
    ),
    _Detector(
        "ACCOUNT",
        re.compile(
            r"\b(?:device\s+id|serial(?:\s+number)?|uuid|guid|imei|mac)\s*"
            r"[:=#-]?\s*([\w:-]{4,})",
            _FLAGS,
        ),
        1,
    ),
    _Detector(
        "ACCOUNT",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            _FLAGS,
        ),
    ),
    _Detector(
        "ACCOUNT",
        re.compile(r"(?<![\w:])(?:[0-9A-F]{2}:){5}[0-9A-F]{2}(?![\w:])", _FLAGS),
    ),
    _Detector(
        "PHONE",
        re.compile(
            r"(?<!\w)(?:\+\d{1,3}[ .-]?(?:\(0\)[ .-]?)?)?"
            r"(?:\(?\d{2,4}\)?[ .-]?){2,4}\d{2,4}(?!\w)"
        ),
    ),
    _Detector(
        "PERSON",
        re.compile(
            rf"\b(?i:my name is|i am|nazywam się|jag heter)\s+"
            rf"({_CAPITALIZED_WORD}(?:\s+{_CAPITALIZED_WORD}){{0,2}})",
        ),
        1,
    ),
    _Detector(
        "ORG",
        re.compile(
            rf"\b(?i:work(?:ing)? at|pracuję w|arbetar på)\s+"
            rf"({_CAPITALIZED_WORD}(?:\s+{_CAPITALIZED_WORD}){{0,3}})",
        ),
        1,
    ),
    _Detector(
        "ORG",
        re.compile(
            rf"\b({_CAPITALIZED_WORD}(?:\s+{_CAPITALIZED_WORD}){{0,4}}\s+"
            r"(?:AB|Ltd\.?|LLC|Inc\.?|GmbH|S\.A\.|Sp\.\s*z\s*o\.o\.))\b"
        ),
        1,
    ),
    _Detector(
        "ORG",
        re.compile(
            rf"\b({_CAPITALIZED_WORD}(?:\s+{_CAPITALIZED_WORD}){{0,2}}\s+"
            r"(?:University|Universitet|Uniwersytet|Institute|Instytut|Foundation|"
            r"Stiftelse|Towarzystwo|Bank|Laboratory|Lab)"
            rf"(?:\s+{_CAPITALIZED_WORD}){{0,2}})\b"
        ),
        1,
    ),
    _Detector(
        "PLACE",
        re.compile(
            rf"\b(?i:live in|mieszkam w|bor i)\s+"
            rf"({_CAPITALIZED_WORD}(?:\s+{_CAPITALIZED_WORD}){{0,2}})",
        ),
        1,
    ),
    _Detector(
        "PERSON",
        re.compile(
            rf"\b((?![A-Z]+_\d{{3}}\b){_CAPITALIZED_WORD}\s+"
            rf"(?![A-Z]+_\d{{3}}\b){_CAPITALIZED_WORD})\b"
        ),
        1,
    ),
)


def _field_category(key: str) -> str | None:
    normalized = key.casefold()
    if normalized in _PRESERVED_FIELDS:
        return None
    for category, parts in _FIELD_CATEGORY_PARTS:
        if normalized in parts:
            return category
    for category, parts in _FIELD_CATEGORY_PARTS:
        if any(normalized.endswith(f"_{part}") for part in parts):
            return category
    return None


class _CaseAnonymizer:
    def __init__(self) -> None:
        self._normalized_to_placeholder: dict[tuple[str, str], str] = {}
        self.reversible_mapping: dict[str, str] = {}
        self.counts: Counter[str] = Counter()

    def _placeholder(self, category: str, original: str) -> str:
        normalized = " ".join(original.split()).casefold()
        if category == "PHONE":
            normalized = re.sub(r"\D", "", normalized)
        elif category == "IP":
            try:
                normalized = ipaddress.ip_address(normalized).compressed
            except ValueError:
                pass
        elif category == "ACCOUNT":
            normalized = re.sub(
                r"^(?:account|user|device|konto|użytkownik|serial|uuid|guid|imei|mac)"
                r"(?:\s*(?:id|identifier|nr|number))?\s*[:=#_-]*\s*",
                "",
                normalized,
            )
        key = (category, normalized)
        placeholder = self._normalized_to_placeholder.get(key)
        if placeholder is None:
            index = (
                sum(
                    1
                    for existing_category, _ in self._normalized_to_placeholder
                    if existing_category == category
                )
                + 1
            )
            placeholder = f"{category}_{index:03d}"
            self._normalized_to_placeholder[key] = placeholder
            self.reversible_mapping[placeholder] = original
        self.counts[category] += 1
        return placeholder

    def _anonymize_text(self, text: str) -> str:
        spans: list[tuple[int, int, int, str, str]] = []
        for priority, detector in enumerate(_DETECTORS):
            for match in detector.pattern.finditer(text):
                start, end = match.span(detector.group)
                original = match.group(detector.group)
                if detector.category == "URL":
                    trimmed = original.rstrip(".,;:!?)]}")
                    end -= len(original) - len(trimmed)
                    original = trimmed
                elif detector.category == "ADDRESS":
                    trimmed = original.rstrip(".,;:!?")
                    end -= len(original) - len(trimmed)
                    original = trimmed
                if detector.validator and not detector.validator(original):
                    continue
                if start != end:
                    spans.append((start, end, priority, detector.category, original))
        accepted: list[tuple[int, int, str, str]] = []
        for start, end, priority, category, original in sorted(
            spans, key=lambda item: (item[2], item[0], -(item[1] - item[0]))
        ):
            if any(
                start < accepted_end and end > accepted_start
                for accepted_start, accepted_end, _, _ in accepted
            ):
                continue
            accepted.append((start, end, category, original))
        accepted.sort(key=lambda item: item[0])
        replacements = [
            (start, end, self._placeholder(category, original))
            for start, end, category, original in accepted
        ]
        result = text
        for start, end, placeholder in reversed(replacements):
            result = f"{result[:start]}{placeholder}{result[end:]}"
        return result

    def anonymize(self, value: Any, *, field_name: str | None = None) -> Any:
        if isinstance(value, dict):
            return {key: self.anonymize(value[key], field_name=key) for key in sorted(value)}
        if isinstance(value, list):
            return [self.anonymize(item, field_name=field_name) for item in value]
        if isinstance(value, str):
            category = _field_category(field_name or "")
            if category and value:
                return self._placeholder(category, value)
            return self._anonymize_text(value)
        return value


def _unresolved_risks(value: Any) -> tuple[str, ...]:
    serialized = canonical_json(value)
    reasons: set[str] = set()
    for detector in _DETECTORS:
        for match in detector.pattern.finditer(serialized):
            original = match.group(detector.group)
            if detector.validator and not detector.validator(original):
                continue
            if all(re.fullmatch(r"[A-Z]+_\d{3}", token) for token in original.split()):
                continue
            reasons.add(f"residual_{detector.category.casefold()}_pattern")
            break
    without_placeholders = re.sub(r"\b[A-Z]+_\d{3}\b", "", serialized)
    if re.search(
        r"\b[A-ZÀ-Ž][\wÀ-ž'-]+\s+[A-ZÀ-Ž][\wÀ-ž'-]+\b",
        without_placeholders,
    ):
        reasons.add("possible_unresolved_named_entity")
    return tuple(sorted(reasons))


def anonymize_structure(value: Any) -> AnonymizationResult:
    anonymizer = _CaseAnonymizer()
    anonymized = anonymizer.anonymize(value)
    return AnonymizationResult(
        anonymized=anonymized,
        reversible_mapping=dict(sorted(anonymizer.reversible_mapping.items())),
        replacement_counts=dict(sorted(anonymizer.counts.items())),
        unresolved_reason_codes=_unresolved_risks(anonymized),
    )


def _structure_counts(value: Any) -> Counter[str]:
    counts: Counter[str] = Counter()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            counts["objects"] += 1
            counts["object_fields"] += len(item)
            for key in sorted(item):
                visit(item[key])
        elif isinstance(item, list):
            counts["arrays"] += 1
            counts["array_items"] += len(item)
            for child in item:
                visit(child)
        elif isinstance(item, str):
            counts["strings"] += 1
        elif item is None:
            counts["nulls"] += 1
        else:
            counts["scalars"] += 1

    visit(value)
    return counts


def build_redaction_report(
    case_id: str,
    original: Any,
    result: AnonymizationResult,
) -> dict[str, Any]:
    before_structure = _structure_counts(original)
    after_structure = _structure_counts(result.anonymized)
    return {
        "report_version": REDACTION_REPORT_VERSION,
        "case_id": case_id,
        "detector_anonymizer_version": ANONYMIZER_VERSION,
        "replacement_counts": result.replacement_counts,
        "unresolved_risk_flags": bool(result.unresolved_reason_codes),
        "unresolved_reason_codes": list(result.unresolved_reason_codes),
        "before_sha256": sha256_json(original),
        "after_sha256": sha256_json(result.anonymized),
        "structural_diff": {
            "structure_preserved": before_structure == after_structure,
            "before_counts": dict(sorted(before_structure.items())),
            "after_counts": dict(sorted(after_structure.items())),
        },
        "human_review_status": "pending",
    }


def load_selected_payloads(
    sidecar_path: Path,
    main_db_path: Path,
    selected: Sequence[SelectedCase],
    contract: RunContract,
) -> list[dict[str, Any]]:
    """Reconstruct selected payloads while keeping both databases immutable."""
    payloads = []
    with (
        contextlib.closing(open_sqlite_immutable(sidecar_path)) as sidecar,
        contextlib.closing(open_sqlite_immutable(main_db_path)) as main,
    ):
        for item in selected:
            unit = sidecar.execute(
                "SELECT * FROM tagging_unit WHERE unit_id = ?",
                (item.candidate.source_unit_id,),
            ).fetchone()
            if unit is None:
                raise BenchmarkError("Selected source unit disappeared")
            manifest = json.loads(unit["segments_json"] or "{}")
            title = ""
            if manifest.get("title_included"):
                row = main.execute(
                    "SELECT title FROM events WHERE context_id = ? AND title IS NOT NULL "
                    "AND title != '' ORDER BY timestamp_start, event_id LIMIT 1",
                    (item.candidate.source_context_id,),
                ).fetchone()
                if row:
                    title = row["title"]

            event_aliases: dict[str, str] = {}
            events = []
            segment_texts = []
            for segment in manifest.get("segments", []):
                event_id = segment["event_id"]
                row = main.execute(
                    "SELECT text, context_id FROM events WHERE event_id = ?", (event_id,)
                ).fetchone()
                if row is None or row["context_id"] != item.candidate.source_context_id:
                    raise BenchmarkError("Selected event is absent or crosses a context boundary")
                text = row["text"] or ""
                start = int(segment.get("start_char", 0))
                end = int(segment.get("end_char", len(text)))
                if not 0 <= start <= end <= len(text):
                    raise BenchmarkError("Selected event has an invalid character range")
                sliced = text[start:end]
                segment_texts.append(sliced)
                alias = event_aliases.setdefault(event_id, f"E{len(event_aliases) + 1}")
                events.append(
                    {
                        "evidence_id": alias,
                        "role": segment["role"],
                        "text": sliced,
                    }
                )
            canonical_content = serialize_semantic_unit(manifest, segment_texts, title)
            reconstructed_hash = compute_reconstructed_content_hash(
                contract.schema_version,
                contract.unit_strategy_version,
                manifest,
                canonical_content,
            )
            if reconstructed_hash != unit["content_hash"]:
                raise BenchmarkError("Selected source unit failed content-hash verification")
            payloads.append(
                {
                    "case_id": item.case_id,
                    "source_unit_id": item.candidate.source_unit_id,
                    "source_content_hash": item.candidate.source_content_hash,
                    "source_context_id": item.candidate.source_context_id,
                    "prompt_version": contract.prompt_version,
                    "schema_version": contract.schema_version,
                    "unit_strategy_version": contract.unit_strategy_version,
                    "context_title": title,
                    "events": events,
                }
            )
    return payloads


def _human_review_preview(anonymized: Mapping[str, Any], contract: RunContract) -> dict[str, Any]:
    """Build a provider-neutral, non-executable view for the later human gate."""
    return {
        "preview_version": HUMAN_REVIEW_PREVIEW_VERSION,
        "case_id": anonymized["case_id"],
        "prompt_version": contract.prompt_version,
        "schema_version": contract.schema_version,
        "unit_strategy_version": contract.unit_strategy_version,
        "context_title": anonymized.get("context_title") or "",
        "events": anonymized["events"],
        "human_review_status": "pending",
        "inference_authorized": False,
    }


def verify_ignored_local_output(repo_root: Path, local_output: Path) -> Path:
    try:
        root = repo_root.resolve(strict=True)
        output = local_output if local_output.is_absolute() else root / local_output
        output = output.resolve(strict=False)
        required_root = (root / LOCAL_ARTIFACT_ROOT).resolve(strict=False)
    except OSError:
        raise BenchmarkError("Unable to resolve the local benchmark output") from None
    if root not in required_root.parents or root not in output.parents:
        raise BenchmarkError("Payload-bearing outputs must stay below the repository root")
    if output != required_root and required_root not in output.parents:
        raise BenchmarkError("Payload-bearing outputs must stay below the local benchmark root")
    relative = output.relative_to(root)
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(relative)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        raise BenchmarkError("Unable to verify the local Git ignore boundary") from None
    if result.returncode != 0:
        raise BenchmarkError("Local benchmark output is not ignored by Git")
    return output


def read_local_benchmark_salt(repo_root: Path, salt_file: Path) -> bytes:
    try:
        root = repo_root.resolve(strict=True)
        resolved = salt_file if salt_file.is_absolute() else root / salt_file
        resolved = resolved.resolve(strict=True)
    except OSError:
        raise BenchmarkError("Benchmark salt must be an existing regular file") from None
    verify_ignored_local_output(root, resolved)
    if not resolved.is_file():
        raise BenchmarkError("Benchmark salt must be an existing regular file")
    try:
        secret_salt = resolved.read_bytes()
    except OSError:
        raise BenchmarkError("Unable to read the local benchmark salt") from None
    if len(secret_salt) < 16:
        raise BenchmarkError("Benchmark salt must contain at least 16 bytes")
    return secret_salt


def _write_new_json(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise BenchmarkError("Benchmark preparation refuses to overwrite an existing artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_new_jsonl(path: Path, rows: Iterable[Any]) -> None:
    if path.exists() or path.is_symlink():
        raise BenchmarkError("Benchmark preparation refuses to overwrite an existing artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def prepare_benchmark_artifacts(
    *,
    repo_root: Path,
    sidecar_path: Path,
    main_db_path: Path,
    secret_salt: bytes,
    approved_source_id: str,
    approved_run_id: str,
    manifest_path: Path = SAFE_MANIFEST_PATH,
    local_output: Path = LOCAL_ARTIFACT_ROOT,
) -> dict[str, int]:
    """Prepare selection/redaction previews only; never execute a model."""
    try:
        root = repo_root.resolve(strict=True)
    except OSError:
        raise BenchmarkError("Repository root must be an existing directory") from None
    requested_manifest = manifest_path if manifest_path.is_absolute() else root / manifest_path
    manifest = Path(os.path.abspath(requested_manifest))
    expected_manifest = root / SAFE_MANIFEST_PATH
    if manifest != expected_manifest:
        raise BenchmarkError("The GitHub-safe manifest path is fixed by the benchmark contract")
    verify_ignored_local_output(root, manifest)
    local = verify_ignored_local_output(root, local_output)
    sidecar_before = _database_snapshot(sidecar_path)
    main_before = _database_snapshot(main_db_path)
    contract, candidates = load_benchmark_candidates(
        sidecar_path,
        secret_salt,
        approved_source_id=approved_source_id,
        approved_run_id=approved_run_id,
    )
    selected, percentile_bounds = select_benchmark_cases(candidates)
    safe_manifest = build_safe_manifest(selected, contract, percentile_bounds)
    originals = load_selected_payloads(sidecar_path, main_db_path, selected, contract)

    anonymized_rows = []
    reports = []
    mappings: dict[str, dict[str, str]] = {}
    previews = []
    for original in originals:
        public_payload = {
            key: value
            for key, value in original.items()
            if key not in {"source_unit_id", "source_content_hash", "source_context_id"}
        }
        result = anonymize_structure(public_payload)
        anonymized_rows.append(result.anonymized)
        mappings[original["case_id"]] = result.reversible_mapping
        reports.append(build_redaction_report(original["case_id"], public_payload, result))
        previews.append(_human_review_preview(result.anonymized, contract))

    artifact_paths = {
        "manifest": manifest,
        "originals": local / "original_selected_payloads.local.jsonl",
        "anonymized": local / "anonymized_payloads.local.jsonl",
        "mappings": local / "reversible_mapping.local.json",
        "reports": local / "redaction_report.local.jsonl",
        "previews": local / "human_review_preview.local.jsonl",
    }
    if manifest_contains_payload_keys(safe_manifest):
        raise BenchmarkError("GitHub-safe manifest contains a forbidden payload key")
    if sidecar_before != _database_snapshot(sidecar_path):
        raise BenchmarkError("Source sidecar changed during read-only benchmark preparation")
    if main_before != _database_snapshot(main_db_path):
        raise BenchmarkError("Main database changed during read-only benchmark preparation")
    if any(path.exists() or path.is_symlink() for path in artifact_paths.values()):
        raise BenchmarkError("Benchmark preparation refuses to overwrite an existing artifact")
    _write_new_json(artifact_paths["manifest"], safe_manifest)
    _write_new_jsonl(artifact_paths["originals"], originals)
    _write_new_jsonl(artifact_paths["anonymized"], anonymized_rows)
    _write_new_json(artifact_paths["mappings"], mappings)
    _write_new_jsonl(artifact_paths["reports"], reports)
    _write_new_jsonl(artifact_paths["previews"], previews)
    return {
        "selected_cases": len(selected),
        "pending_human_reviews": sum(
            report["human_review_status"] == "pending" for report in reports
        ),
        "unresolved_risk_reports": sum(report["unresolved_risk_flags"] for report in reports),
        **{
            category: sum(item.category == category for item in selected)
            for category in CATEGORY_PRIORITY
        },
    }


def manifest_contains_payload_keys(manifest: Mapping[str, Any]) -> bool:
    """Conservative test/audit helper for the GitHub-visible artifact."""
    forbidden = {
        "text",
        "title",
        "content",
        "events",
        "event_id",
        "context_id",
        "unit_id",
        "reversible_mapping",
        "source_unit_id",
        "source_context_id",
    }

    def keys(value: Any) -> Iterator[str]:
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    return any(key in forbidden for key in keys(manifest))
