import contextlib
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from scripts.semantic_tagger.benchmark import (
    CATEGORY_PRIORITY,
    HISTORICAL_EVIDENCE_PATH,
    HUMAN_REVIEW_PREVIEW_VERSION,
    INVENTORY_PATH,
    BenchmarkError,
    BenchmarkQuotaError,
    CandidateDiagnostics,
    RunContract,
    audit_historical_evidence,
    anonymize_structure,
    build_redaction_report,
    build_safe_manifest,
    file_sha256,
    inventory_sidecars,
    load_benchmark_candidates,
    make_candidate,
    manifest_contains_payload_keys,
    open_sqlite_immutable,
    opaque_run_id,
    prepare_benchmark_artifacts,
    read_local_benchmark_salt,
    select_benchmark_cases,
    sidecar_source_id,
    verify_ignored_local_output,
    write_sidecar_inventory,
)
from scripts.semantic_tagger.benchmark_cli import main as benchmark_cli_main
from scripts.semantic_tagger.prompt_budget import (
    PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
    PROMPT_ESTIMATOR_VERSION,
)
from scripts.semantic_tagger.unit_planner import PROMPT_BUDGET_STRATEGY_VERSION
from scripts.semantic_tagger.unit_serializer import (
    compute_v3_content_hash,
    serialize_semantic_unit,
)


SALT = b"synthetic-benchmark-salt-value"


def _diagnostics(
    *,
    prompt_tokens,
    relations=0,
    evidence=0,
    chunks=1,
    empty=False,
    rejected=False,
    generic=0,
    duplicates=0,
    accepted=True,
):
    return CandidateDiagnostics(
        relation_count=relations,
        relation_evidence_count=evidence,
        planned_chunk_count=chunks,
        estimated_prompt_tokens=prompt_tokens,
        prompt_limit=5632,
        historically_empty=empty,
        historically_rejected=rejected,
        generic_label_count=generic,
        duplicate_label_count=duplicates,
        accepted=accepted,
    )


def _candidate(index, diagnostics):
    return make_candidate(
        secret_salt=SALT,
        source_unit_id=f"synthetic-unit-{index}",
        source_content_hash=hashlib.sha256(f"content-{index}".encode()).hexdigest(),
        source_context_id=f"synthetic-context-{index}",
        diagnostics=diagnostics,
    )


def _quota_candidates():
    return [
        _candidate(1, _diagnostics(prompt_tokens=100, relations=3, evidence=5, generic=1)),
        _candidate(2, _diagnostics(prompt_tokens=200, relations=2, evidence=4, chunks=2)),
        _candidate(3, _diagnostics(prompt_tokens=300, chunks=3)),
        _candidate(4, _diagnostics(prompt_tokens=400, chunks=2)),
        _candidate(5, _diagnostics(prompt_tokens=5100)),
        _candidate(6, _diagnostics(prompt_tokens=5200)),
        _candidate(7, _diagnostics(prompt_tokens=600, empty=True, accepted=False)),
        _candidate(8, _diagnostics(prompt_tokens=700, rejected=True, accepted=False)),
        _candidate(9, _diagnostics(prompt_tokens=1000, generic=2)),
        _candidate(10, _diagnostics(prompt_tokens=1100, duplicates=1)),
        _candidate(11, _diagnostics(prompt_tokens=800)),
        _candidate(12, _diagnostics(prompt_tokens=900)),
    ]


def _contract(sidecar_hash="a" * 64):
    return RunContract(
        run_id="synthetic-run",
        prompt_version="semantic-hybrid-v3",
        schema_version="semantic-tags-v3",
        unit_strategy_version=PROMPT_BUDGET_STRATEGY_VERSION,
        model_name="qwen3:14b",
        settings={
            "think": False,
            "temperature": 0,
            "stream": False,
            "num_ctx": 8192,
            "max_prompt_tokens": 5632,
            "num_predict": 1536,
            "safety_margin": 1024,
            "seed": 42,
            "chunk_overlap_characters": 256,
            "chunk_boundary_backtrack_characters": 256,
        },
        source_sidecar_provenance_hash=sidecar_hash,
    )


def test_selection_has_exact_quotas_overlap_accounting_and_repeatability():
    candidates = _quota_candidates()
    selected, bounds = select_benchmark_cases(candidates)
    repeated, repeated_bounds = select_benchmark_cases(list(reversed(candidates)))

    assert len(selected) == 12
    assert {
        category: sum(case.category == category for case in selected)
        for category in CATEGORY_PRIORITY
    } == {category: 2 for category in CATEGORY_PRIORITY}
    assert [(case.case_id, case.category) for case in selected] == [
        (case.case_id, case.category) for case in repeated
    ]
    assert bounds == repeated_bounds
    relation_cases = [case for case in selected if case.category == "relation_rich"]
    assert any(
        "historical_generic_or_duplicate_labels" in case.overlap_reason_codes
        for case in relation_cases
    )
    assert any(
        "planner_predicted_multiple_chunks" in case.overlap_reason_codes for case in relation_cases
    )


def test_selection_stops_instead_of_relaxing_a_quota():
    with pytest.raises(BenchmarkQuotaError, match="exactly 2 are required"):
        select_benchmark_cases(_quota_candidates()[:-1])


def test_relation_rich_requires_recorded_relation_evidence():
    candidates = _quota_candidates()
    candidates[1] = _candidate(
        2,
        _diagnostics(prompt_tokens=200, relations=2, evidence=0, chunks=2),
    )

    with pytest.raises(BenchmarkQuotaError, match="Category relation_rich has 1 eligible"):
        select_benchmark_cases(candidates)


def test_anonymizer_covers_pii_categories_and_reuses_case_local_placeholders():
    original = {
        "person_name": "Ada Lovelace",
        "email": "ada@example.test",
        "phone": "+46 70 123 45 67",
        "account_id": "account-7788",
        "device_id": "account-7788",
        "url": "https://example.test/private",
        "path": "/home/ada/private/file.txt",
        "address": "12 Example Street, 12345",
        "location": "Stockholm",
        "organization": "Example Research Lab",
        "ip_address": "192.0.2.17",
        "notes": "Contact ada@example.test on 2026-08-03.",
    }
    result = anonymize_structure(original)
    serialized = json.dumps(result.anonymized, ensure_ascii=False)

    for value in original.values():
        if value == original["device_id"]:
            continue
        assert value not in serialized
    assert result.anonymized["email"] == "EMAIL_001"
    assert "EMAIL_001" in result.anonymized["notes"]
    assert result.anonymized["account_id"] == result.anonymized["device_id"]
    assert set(result.replacement_counts) >= {
        "PERSON",
        "EMAIL",
        "PHONE",
        "ACCOUNT",
        "URL",
        "PATH",
        "ADDRESS",
        "PLACE",
        "ORG",
        "IP",
        "DATE",
    }
    assert anonymize_structure(original).anonymized == result.anonymized


def test_anonymizer_handles_polish_swedish_unicode_and_prose_identifiers():
    text = (
        "Nazywam się Łukasz Żółć i pracuję w Polskie Towarzystwo Lab. "
        "Mieszkam przy ul. Długiej 12, 00-001 Warszawa. "
        "Jag heter Åsa Öberg och arbetar på Nordisk Forskning AB. "
        "Adressen är Storgatan 12, 123 45 Stockholm. "
        "Pliki: /home/łukasz/żółć.txt oraz C:\\Users\\Åsa\\privat.txt. "
        "Łącza: ftp://example.test/private, www.example.test/secret. "
        "Sieć: 2001:db8::1. Telefon: +46 (0)70-123 45 67."
    )

    result = anonymize_structure({"text": text})
    anonymized = result.anonymized["text"]

    for private_value in (
        "Łukasz Żółć",
        "Polskie Towarzystwo Lab",
        "ul. Długiej 12, 00-001 Warszawa",
        "Åsa Öberg",
        "Nordisk Forskning AB",
        "Storgatan 12, 123 45 Stockholm",
        "/home/łukasz/żółć.txt",
        "C:\\Users\\Åsa\\privat.txt",
        "ftp://example.test/private",
        "www.example.test/secret",
        "2001:db8::1",
        "+46 (0)70-123 45 67",
    ):
        assert private_value not in anonymized
    assert "PERSON_001" in anonymized
    assert "PERSON_002" in anonymized
    assert "ORG_001" in anonymized
    assert "ORG_002" in anonymized
    assert "ADDRESS_001" in anonymized
    assert "ADDRESS_002" in anonymized
    assert "PATH_001" in anonymized
    assert "PATH_002" in anonymized
    assert "URL_001" in anonymized
    assert "URL_002" in anonymized
    assert "IP_001" in anonymized
    assert "PHONE_001" in anonymized
    assert "Nazywam się PERSON_001 i pracuję w ORG_001." in anonymized
    assert "Jag heter PERSON_002 och arbetar på ORG_002." in anonymized


def test_placeholder_assignment_is_left_to_right_and_normalizes_repeated_account_ids():
    original = {
        "account_id": "account-7788",
        "device_id": "account-7788",
        "text": ("Nazywam się Łukasz Żółć. Jag heter Åsa Öberg. account id: account-7788"),
    }

    result = anonymize_structure(original)

    assert result.anonymized["account_id"] == "ACCOUNT_001"
    assert result.anonymized["device_id"] == "ACCOUNT_001"
    assert "account id: ACCOUNT_001" in result.anonymized["text"]
    assert result.anonymized["text"].index("PERSON_001") < result.anonymized["text"].index(
        "PERSON_002"
    )


def test_redaction_report_has_no_original_values_and_preserves_structure():
    original = {
        "case_id": "case-synthetic",
        "events": [{"evidence_id": "E1", "role": "user", "text": "ada@example.test"}],
    }
    result = anonymize_structure(original)
    report = build_redaction_report("case-synthetic", original, result)
    serialized = json.dumps(report)

    assert "ada@example.test" not in serialized
    assert report["human_review_status"] == "pending"
    assert report["structural_diff"]["structure_preserved"] is True
    assert len(report["before_sha256"]) == len(report["after_sha256"]) == 64


def test_safe_manifest_excludes_payload_and_reversible_identifiers():
    selected, bounds = select_benchmark_cases(_quota_candidates())
    manifest = build_safe_manifest(selected, _contract(), bounds)
    serialized = json.dumps(manifest)

    assert manifest["case_count"] == 12
    assert not manifest_contains_payload_keys(manifest)
    assert "synthetic-unit" not in serialized
    assert "synthetic-context" not in serialized
    assert "content-" not in serialized
    assert "source_sidecar_sha256" not in manifest
    assert manifest["source_sidecar_provenance_hash"] == "a" * 64


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=path, check=True)
    (path / ".gitignore").write_text("/.local/\n", encoding="utf-8")


def test_local_payload_directory_must_be_below_verified_ignored_root(tmp_path):
    _init_git_repo(tmp_path)
    expected = tmp_path / ".local" / "semantic_tagger_benchmark"
    expected.mkdir(parents=True)
    salt_file = expected / "selector_salt.local"
    salt_file.write_bytes(SALT)

    assert verify_ignored_local_output(tmp_path, expected) == expected
    assert read_local_benchmark_salt(tmp_path, salt_file) == SALT
    with pytest.raises(ValueError, match="must stay below"):
        verify_ignored_local_output(tmp_path, tmp_path / "tracked-output")
    outside_salt = tmp_path / "outside-salt"
    outside_salt.write_bytes(SALT)
    with pytest.raises(ValueError, match="must stay below"):
        read_local_benchmark_salt(tmp_path, outside_salt)


def _stored_output(labels, relations=()):
    concepts = [
        {
            "concept_id": f"C{index}",
            "surface_label": label,
            "preferred_label": label,
            "language": "en",
            "entity_types": ["topic"],
            "domains": ["general"],
            "context_roles": [],
            "importance": 0.5,
            "confidence": 0.5,
            "evidence_event_ids": [],
        }
        for index, label in enumerate(labels, start=1)
    ]
    return {
        "schema_version": "semantic-tags-v3",
        "languages": ["en"],
        "content_types": ["prose"],
        "unit_quality": "meaningful" if concepts else "junk",
        "concepts": concepts,
        "relations": [
            {
                "subject_concept_id": subject,
                "predicate": predicate,
                "object_concept_id": object_,
                "confidence": 0.5,
                "evidence_event_ids": evidence,
            }
            for subject, predicate, object_, evidence in relations
        ],
    }


def _create_synthetic_databases(root: Path) -> tuple[Path, Path]:
    sidecar = root / "semantic_tagger_synthetic.local.sqlite3"
    main_db = root / "synthetic-main.sqlite3"
    settings = json.dumps(
        {
            "think": False,
            "temperature": 0,
            "stream": False,
            "num_ctx": 8192,
            "max_prompt_tokens": 5632,
            "num_predict": 1536,
            "safety_margin": 1024,
            "seed": 42,
            "prompt_estimator_version": PROMPT_ESTIMATOR_VERSION,
            "chunk_overlap_characters": 256,
            "chunk_boundary_backtrack_characters": 256,
        }
    )
    with contextlib.closing(sqlite3.connect(sidecar)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE tagging_run (
                run_id TEXT, created_at TEXT, prompt_version TEXT, schema_version TEXT,
                unit_strategy_version TEXT, model_name TEXT, settings_json TEXT
            );
            CREATE TABLE tagging_unit (
                unit_id TEXT, context_id TEXT, content_hash TEXT, segments_json TEXT,
                estimated_token_count INTEGER
            );
            CREATE TABLE tagging_job (
                run_id TEXT, unit_id TEXT, status TEXT, output_json TEXT, error_code TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO tagging_run VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "synthetic-run",
                "2026-08-03T00:00:00",
                "semantic-hybrid-v3",
                "semantic-tags-v3",
                PROMPT_BUDGET_STRATEGY_VERSION,
                "qwen3:14b",
                settings,
            ),
        )

    with contextlib.closing(sqlite3.connect(main_db)) as connection, connection:
        connection.execute(
            "CREATE TABLE events (event_id TEXT, context_id TEXT, timestamp_start TEXT, title TEXT, text TEXT)"
        )

    prompt_sizes = [100, 200, 300, 400, 5100, 5200, 600, 700, 1000, 1100, 800, 900]
    for index in range(1, 13):
        context_id = f"ctx-{index}"
        event_id = f"event-{index}"
        text = f"synthetic message {index} for ada{index}@example.test"
        is_chunk = index in {3, 4}
        if is_chunk:
            context_id = "ctx-chunk"
            event_id = "event-chunk"
            text = "synthetic chunk one synthetic chunk two"
            start, end = (0, 20) if index == 3 else (20, len(text))
        else:
            start, end = 0, len(text)
        manifest = {
            "title_included": False,
            "title_source": context_id,
            "unit_strategy_version": PROMPT_BUDGET_STRATEGY_VERSION,
            "prompt_version": "semantic-hybrid-v3",
            "schema_version": "semantic-tags-v3",
            "prompt_estimator_version": PROMPT_ESTIMATOR_VERSION,
            "chunk_overlap_characters": 256,
            "chunk_boundary_backtrack_characters": 256,
            "prompt_budget_basis": PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
            "prompt_budget": {
                "initial_prompt_estimate": prompt_sizes[index - 1],
                "maximum_retry_prompt_estimate": prompt_sizes[index - 1],
                "worst_case_prompt_estimate": prompt_sizes[index - 1],
                "maximum_retry_variant": "attempt_2_schema_retry",
                "worst_case_prompt_variant": "attempt_1",
                "variant_estimates": {
                    "attempt_1": prompt_sizes[index - 1],
                    "attempt_2_schema_retry": prompt_sizes[index - 1],
                    "attempt_2_facets_missing": prompt_sizes[index - 1],
                    "attempt_3_reduced_output": prompt_sizes[index - 1],
                },
            },
            "segments": [
                {
                    "event_id": event_id,
                    "context_id": context_id,
                    "role": "user",
                    "start_char": start,
                    "end_char": end,
                    "sequence_in_unit": 0,
                    "is_overlap": is_chunk and index == 4,
                    "is_chunk": is_chunk,
                    "overlap_from_previous_characters": 0,
                }
            ],
            "oversized_single_event": is_chunk,
        }
        canonical = serialize_semantic_unit(manifest, [text[start:end]], "")
        content_hash = compute_v3_content_hash(
            "semantic-tags-v3", PROMPT_BUDGET_STRATEGY_VERSION, manifest, canonical
        )
        if index == 1:
            output = _stored_output(
                ["alpha", "beta"],
                [("C1", "related_to", "C2", [event_id])],
            )
            status, error = "done", None
        elif index == 2:
            output = _stored_output(
                ["gamma", "delta"],
                [("C1", "related_to", "C2", [event_id, event_id])],
            )
            status, error = "done", None
        elif index == 7:
            output, status, error = _stored_output([]), "done", None
        elif index == 8:
            output, status, error = {}, "failed", "validation_error"
        elif index in {9, 10}:
            label = "system" if index == 9 else "project"
            output = _stored_output([label])
            status, error = "done", None
        else:
            output = _stored_output([f"concept_{index}"])
            status, error = "done", None
        with contextlib.closing(sqlite3.connect(sidecar)) as connection, connection:
            connection.execute(
                "INSERT INTO tagging_unit VALUES (?, ?, ?, ?, ?)",
                (
                    f"unit-{index}",
                    context_id,
                    content_hash,
                    json.dumps(manifest),
                    prompt_sizes[index - 1],
                ),
            )
            connection.execute(
                "INSERT INTO tagging_job VALUES (?, ?, ?, ?, ?)",
                ("synthetic-run", f"unit-{index}", status, json.dumps(output), error),
            )
        if index not in {4}:
            with contextlib.closing(sqlite3.connect(main_db)) as connection, connection:
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?, '', ?)",
                    (event_id, context_id, f"2026-01-{index:02d}", text),
                )
    return sidecar, main_db


def _approved_ids(sidecar: Path, run_id: str = "synthetic-run") -> tuple[str, str]:
    source_id = sidecar_source_id(sidecar, SALT)
    return source_id, opaque_run_id(SALT, source_id, run_id)


def test_end_to_end_dry_run_preserves_databases_and_creates_no_wal_or_shm(tmp_path):
    _init_git_repo(tmp_path)
    sidecar, main_db = _create_synthetic_databases(tmp_path)
    before = {path: file_sha256(path) for path in (sidecar, main_db)}
    approved_source_id, approved_run_id = _approved_ids(sidecar)

    results = prepare_benchmark_artifacts(
        repo_root=tmp_path,
        sidecar_path=sidecar,
        main_db_path=main_db,
        secret_salt=SALT,
        approved_source_id=approved_source_id,
        approved_run_id=approved_run_id,
    )

    assert results["selected_cases"] == 12
    assert all(results[category] == 2 for category in CATEGORY_PRIORITY)
    assert {path: file_sha256(path) for path in (sidecar, main_db)} == before
    assert not any(
        Path(f"{path}{suffix}").exists()
        for path in (sidecar, main_db)
        for suffix in ("-wal", "-shm")
    )
    manifest = json.loads(
        (tmp_path / ".local/semantic_tagger_benchmark/manifest.local.json").read_text()
    )
    assert not manifest_contains_payload_keys(manifest)
    assert "model_name" not in manifest
    assert manifest["source_sidecar_provenance_hash"] != before[sidecar]
    local = tmp_path / ".local/semantic_tagger_benchmark"
    assert {path.name for path in local.iterdir()} == {
        "original_selected_payloads.local.jsonl",
        "anonymized_payloads.local.jsonl",
        "reversible_mapping.local.json",
        "redaction_report.local.jsonl",
        "human_review_preview.local.jsonl",
        "manifest.local.json",
    }
    redacted_outputs = "\n".join(
        (local / filename).read_text(encoding="utf-8")
        for filename in (
            "anonymized_payloads.local.jsonl",
            "redaction_report.local.jsonl",
            "human_review_preview.local.jsonl",
        )
    )
    for index in range(1, 13):
        assert f"ada{index}@example.test" not in redacted_outputs
        assert f"ctx-{index}" not in redacted_outputs
        assert f"event-{index}" not in redacted_outputs
        assert f"unit-{index}" not in redacted_outputs
    assert "ctx-chunk" not in redacted_outputs
    assert "event-chunk" not in redacted_outputs
    for artifact in local.iterdir():
        relative = artifact.relative_to(tmp_path)
        assert (
            subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(relative)],
                cwd=tmp_path,
                check=False,
            ).returncode
            == 0
        )


def test_read_only_candidate_loading_does_not_mutate_source_sidecar(tmp_path):
    sidecar, main_db = _create_synthetic_databases(tmp_path)
    before = {path: file_sha256(path) for path in (sidecar, main_db)}
    approved_source_id, approved_run_id = _approved_ids(sidecar)

    contract, candidates = load_benchmark_candidates(
        sidecar,
        SALT,
        approved_source_id=approved_source_id,
        approved_run_id=approved_run_id,
    )
    with contextlib.closing(open_sqlite_immutable(main_db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 11

    assert contract.model_name == "qwen3:14b"
    assert len(candidates) == 12
    assert {path: file_sha256(path) for path in (sidecar, main_db)} == before
    assert not sidecar.with_name(f"{sidecar.name}-wal").exists()
    assert not main_db.with_name(f"{main_db.name}-shm").exists()


def test_candidate_loading_rejects_stale_planner_diagnostics(tmp_path):
    sidecar, _ = _create_synthetic_databases(tmp_path)
    with contextlib.closing(sqlite3.connect(sidecar)) as connection, connection:
        row = connection.execute(
            "SELECT segments_json FROM tagging_unit WHERE unit_id = 'unit-1'"
        ).fetchone()
        manifest = json.loads(row[0])
        manifest["prompt_budget"]["worst_case_prompt_estimate"] += 1
        connection.execute(
            "UPDATE tagging_unit SET segments_json = ? WHERE unit_id = 'unit-1'",
            (json.dumps(manifest),),
        )

    with pytest.raises(BenchmarkError, match="authoritative planner value"):
        approved_source_id, approved_run_id = _approved_ids(sidecar)
        load_benchmark_candidates(
            sidecar,
            SALT,
            approved_source_id=approved_source_id,
            approved_run_id=approved_run_id,
        )


def test_non_validation_runtime_failure_is_not_a_historical_rejection(tmp_path):
    sidecar, _ = _create_synthetic_databases(tmp_path)
    with contextlib.closing(sqlite3.connect(sidecar)) as connection, connection:
        connection.execute(
            "UPDATE tagging_job SET error_code = 'ollama_error' WHERE unit_id = 'unit-8'"
        )

    approved_source_id, approved_run_id = _approved_ids(sidecar)
    _, candidates = load_benchmark_candidates(
        sidecar,
        SALT,
        approved_source_id=approved_source_id,
        approved_run_id=approved_run_id,
    )
    candidate = next(item for item in candidates if item.source_unit_id == "unit-8")

    assert candidate.diagnostics.historically_rejected is False


def test_generic_label_diagnostics_normalize_polish_and_swedish_unicode(tmp_path):
    sidecar, _ = _create_synthetic_databases(tmp_path)
    with contextlib.closing(sqlite3.connect(sidecar)) as connection, connection:
        connection.execute(
            "UPDATE tagging_job SET output_json = ? WHERE unit_id = 'unit-11'",
            (json.dumps(_stored_output(["lösning", "rozwiązanie"])),),
        )

    approved_source_id, approved_run_id = _approved_ids(sidecar)
    _, candidates = load_benchmark_candidates(
        sidecar,
        SALT,
        approved_source_id=approved_source_id,
        approved_run_id=approved_run_id,
    )
    candidate = next(item for item in candidates if item.source_unit_id == "unit-11")

    assert candidate.diagnostics.generic_label_count == 2


def test_cli_failure_does_not_echo_private_paths(tmp_path, capsys):
    private_path = tmp_path / "Łukasz-private-selector-salt"

    exit_code = benchmark_cli_main(
        [
            "inventory",
            "--sidecar",
            str(tmp_path / "private-sidecar.sqlite3"),
            "--salt-file",
            str(private_path),
            "--repo-root",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert str(private_path) not in captured.err
    assert "checkpoint_error=bounded_offline_operation_failed" in captured.err


def _inventory_settings() -> str:
    return json.dumps(
        {
            "think": False,
            "temperature": 0,
            "stream": False,
            "num_ctx": 8192,
            "max_prompt_tokens": 5632,
            "num_predict": 1536,
            "safety_margin": 1024,
            "seed": 42,
            "prompt_estimator_version": PROMPT_ESTIMATOR_VERSION,
            "chunk_overlap_characters": 256,
            "chunk_boundary_backtrack_characters": 256,
        },
        sort_keys=True,
    )


def _create_inventory_sidecar(
    root: Path,
    name: str,
    runs: list[dict],
) -> Path:
    sidecar = root / name
    with contextlib.closing(sqlite3.connect(sidecar)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE sidecar_meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO sidecar_meta VALUES ('schema_version', '4');
            CREATE TABLE tagging_run (
                run_id TEXT PRIMARY KEY,
                prompt_version TEXT,
                schema_version TEXT,
                unit_strategy_version TEXT,
                model_name TEXT,
                model_digest TEXT,
                ollama_version TEXT,
                source_database_fingerprint TEXT,
                settings_json TEXT
            );
            CREATE TABLE tagging_job (
                run_id TEXT,
                status TEXT,
                output_json TEXT
            );
            CREATE TABLE worker_state (run_id TEXT, status TEXT);
            """
        )
        for run in runs:
            connection.execute(
                "INSERT INTO tagging_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run["run_id"],
                    run.get("prompt_version", "semantic-hybrid-v3"),
                    run.get("schema_version", "semantic-tags-v3"),
                    run.get("unit_strategy_version", PROMPT_BUDGET_STRATEGY_VERSION),
                    "qwen3:14b",
                    "sha256:" + "a" * 64,
                    "0.30.10",
                    "private-source-fingerprint-must-not-leak",
                    run.get("settings_json", _inventory_settings()),
                ),
            )
            for status in run.get("jobs", ["done"]):
                connection.execute(
                    "INSERT INTO tagging_job VALUES (?, ?, ?)",
                    (
                        run["run_id"],
                        status,
                        '{"text":"private payload must not leak"}',
                    ),
                )
    return sidecar


def _find_run(report: dict, source_name: str, run_id: str) -> dict:
    source = next(item for item in report["sources"] if item.get("source_filename") == source_name)
    expected = opaque_run_id(SALT, source["source_id"], run_id)
    return next(item for item in source["runs"] if item["opaque_run_id"] == expected)


def test_inventory_reports_exact_contract_reasons_multiple_runs_and_no_payload(tmp_path):
    matching = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_matching.local.sqlite3",
        [{"run_id": "matching-private-run", "jobs": ["done"]}],
    )
    prompt_mismatch = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_prompt_mismatch.local.sqlite3",
        [
            {
                "run_id": "prompt-mismatch-private-run",
                "prompt_version": "semantic-hybrid-v2",
            }
        ],
    )
    schema_mismatch = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_schema_mismatch.local.sqlite3",
        [
            {
                "run_id": "schema-mismatch-private-run",
                "schema_version": "semantic-tags-v2",
            }
        ],
    )
    planner_mismatch = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_planner_mismatch.local.sqlite3",
        [
            {
                "run_id": "planner-mismatch-private-run",
                "unit_strategy_version": "unit-v2-whole-events",
            }
        ],
    )
    multiple = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_multiple.local.sqlite3",
        [
            {"run_id": "terminal-failed-private-run", "jobs": ["done", "failed"]},
            {"run_id": "partial-private-run", "jobs": ["done", "pending"]},
        ],
    )
    allowlist = [matching, prompt_mismatch, schema_mismatch, planner_mismatch, multiple]

    report = inventory_sidecars(
        repo_root=tmp_path,
        sidecar_allowlist=allowlist,
        secret_salt=SALT,
    )
    repeated = inventory_sidecars(
        repo_root=tmp_path,
        sidecar_allowlist=list(reversed(allowlist)),
        secret_salt=SALT,
    )

    assert report == repeated
    assert report["sidecar_count"] == 5
    assert report["unsafe_sidecar_count"] == 0
    assert report["compatible_run_count"] == 2
    assert _find_run(report, matching.name, "matching-private-run")[
        "compatibility_reason_codes"
    ] == ["compatible_frozen_semantic_hybrid_v3_contract"]
    assert _find_run(report, prompt_mismatch.name, "prompt-mismatch-private-run")[
        "compatibility_reason_codes"
    ] == ["prompt_version_mismatch"]
    assert _find_run(report, schema_mismatch.name, "schema-mismatch-private-run")[
        "compatibility_reason_codes"
    ] == ["schema_version_mismatch"]
    assert _find_run(report, planner_mismatch.name, "planner-mismatch-private-run")[
        "compatibility_reason_codes"
    ] == ["planner_strategy_mismatch"]
    terminal = _find_run(report, multiple.name, "terminal-failed-private-run")
    assert terminal["compatible"] is True
    assert terminal["terminal_job_counts"] == {"done": 1, "failed": 1}
    partial = _find_run(report, multiple.name, "partial-private-run")
    assert partial["compatible"] is False
    assert partial["compatibility_reason_codes"] == ["run_has_nonterminal_jobs"]
    serialized = json.dumps(report, ensure_ascii=False)
    for forbidden in (
        "matching-private-run",
        "private-source-fingerprint-must-not-leak",
        "private payload must not leak",
        str(tmp_path),
        "output_json",
    ):
        assert forbidden not in serialized


def test_inventory_rejects_nonzero_wal_and_active_handle(tmp_path):
    wal_sidecar = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_nonzero_wal.local.sqlite3",
        [{"run_id": "wal-run"}],
    )
    Path(f"{wal_sidecar}-wal").write_bytes(b"synthetic-nonzero-wal")
    active_sidecar = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_active_handle.local.sqlite3",
        [{"run_id": "active-run"}],
    )

    with active_sidecar.open("rb"):
        report = inventory_sidecars(
            repo_root=tmp_path,
            sidecar_allowlist=[wal_sidecar, active_sidecar],
            secret_salt=SALT,
        )

    assert report["unsafe_sidecar_count"] == 2
    wal_report = next(
        item for item in report["sources"] if item.get("source_filename") == wal_sidecar.name
    )
    active_report = next(
        item for item in report["sources"] if item.get("source_filename") == active_sidecar.name
    )
    assert wal_report["companion_files"]["wal"] == "nonzero"
    assert wal_report["source_reason_codes"] == ["unsafe_nonzero_wal"]
    assert active_report["source_reason_codes"] == ["unsafe_active_open_handle"]
    assert wal_report["sqlite_integrity"] == "not_checked"
    assert active_report["sqlite_integrity"] == "not_checked"


def test_inventory_sanitizes_corrupt_database_errors(tmp_path):
    sidecar = tmp_path / "semantic_tagger_corrupt.local.sqlite3"
    private_marker = b"private database content and exception detail must not leak"
    sidecar.write_bytes(private_marker)

    report = inventory_sidecars(
        repo_root=tmp_path,
        sidecar_allowlist=[sidecar],
        secret_salt=SALT,
    )

    source = report["sources"][0]
    assert source["source_status"] == "rejected"
    assert source["sqlite_integrity"] == "failed"
    assert source["source_reason_codes"] == ["sqlite_integrity_failed"]
    serialized = json.dumps(report)
    assert private_marker.decode() not in serialized
    assert str(tmp_path) not in serialized


def test_inventory_cli_writes_only_sanitized_ignored_output(tmp_path, capsys):
    _init_git_repo(tmp_path)
    sidecar = _create_inventory_sidecar(
        tmp_path,
        "semantic_tagger_cli.local.sqlite3",
        [{"run_id": "private-cli-run", "jobs": ["done", "failed"]}],
    )
    local_root = tmp_path / ".local" / "semantic_tagger_benchmark"
    local_root.mkdir(parents=True)
    salt_file = local_root / "selector_salt.local"
    salt_file.write_bytes(SALT)

    exit_code = benchmark_cli_main(
        [
            "inventory",
            "--sidecar",
            str(sidecar),
            "--salt-file",
            str(salt_file),
            "--repo-root",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert str(tmp_path) not in captured.out
    assert "private-cli-run" not in captured.out
    inventory_path = tmp_path / INVENTORY_PATH
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert inventory["compatible_run_count"] == 1
    serialized = json.dumps(inventory)
    assert "private-cli-run" not in serialized
    assert "private payload must not leak" not in serialized
    assert (
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(INVENTORY_PATH)],
            cwd=tmp_path,
            check=False,
        ).returncode
        == 0
    )


def test_historical_evidence_audit_maps_validated_history_and_excludes_runtime_failures(
    tmp_path,
):
    _init_git_repo(tmp_path)
    sidecar, main_db = _create_synthetic_databases(tmp_path)
    local_root = tmp_path / ".local" / "semantic_tagger_benchmark"
    local_root.mkdir(parents=True)
    salt_file = local_root / "selector_salt.local"
    salt_file.write_bytes(SALT)
    with contextlib.closing(sqlite3.connect(sidecar)) as connection, connection:
        connection.execute(
            "INSERT INTO tagging_run VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "historical-run",
                "2026-08-02T00:00:00",
                "semantic-hybrid-v2",
                "semantic-tags-v3",
                "unit-v2-whole-events",
                "qwen3:14b",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO tagging_job VALUES (?, ?, ?, ?, ?)",
            (
                "historical-run",
                "unit-11",
                "done",
                json.dumps(_stored_output(["system"])),
                None,
            ),
        )
        connection.execute(
            "INSERT INTO tagging_job VALUES (?, ?, ?, ?, ?)",
            ("historical-run", "unit-12", "failed", "{}", "ollama_error"),
        )

    before = {path: file_sha256(path) for path in (sidecar, main_db)}
    write_sidecar_inventory(
        repo_root=tmp_path,
        sidecar_allowlist=[sidecar],
        secret_salt=SALT,
    )
    report = audit_historical_evidence(
        repo_root=tmp_path,
        sidecar_allowlist=[sidecar],
        protected_databases=[main_db],
        secret_salt=SALT,
    )

    assert report["historical_record_counts"] == {
        "total": 14,
        "terminal": 14,
        "mapped_deterministically": 13,
        "excluded": 1,
    }
    assert report["historical_excluded_reason_counts"] == {
        "historical_runtime_transport_or_infrastructure_failure": 1
    }
    assert report["candidate_pool_counts"]["problematic_or_generic_labels"] == 3
    assert any(
        item["prompt_equivalence_code"] == "historical_prompt_version_not_provider_equivalent"
        for item in report["mapped_historical_provenance_counts"]
    )
    assert "selected_cases" not in report
    assert {path: file_sha256(path) for path in (sidecar, main_db)} == before
    assert not any(
        Path(f"{path}{suffix}").exists()
        for path in (sidecar, main_db)
        for suffix in ("-wal", "-shm")
    )
    serialized = (tmp_path / HISTORICAL_EVIDENCE_PATH).read_text(encoding="utf-8")
    for forbidden in (
        "historical-run",
        "unit-11",
        "unit-12",
        "system",
        "ollama_error",
        str(tmp_path),
    ):
        assert forbidden not in serialized


def test_future_selector_requires_matching_explicit_opaque_approvals(tmp_path):
    sidecar, _ = _create_synthetic_databases(tmp_path)
    source_id, run_id = _approved_ids(sidecar)

    with pytest.raises(BenchmarkError, match="approved sidecar ID"):
        load_benchmark_candidates(
            sidecar,
            SALT,
            approved_source_id="0" * 64,
            approved_run_id=run_id,
        )
    with pytest.raises(BenchmarkError, match="opaque run ID"):
        load_benchmark_candidates(
            sidecar,
            SALT,
            approved_source_id=source_id,
            approved_run_id="0" * 64,
        )


def test_human_review_preview_is_provider_neutral(tmp_path):
    _init_git_repo(tmp_path)
    sidecar, main_db = _create_synthetic_databases(tmp_path)
    source_id, run_id = _approved_ids(sidecar)

    prepare_benchmark_artifacts(
        repo_root=tmp_path,
        sidecar_path=sidecar,
        main_db_path=main_db,
        secret_salt=SALT,
        approved_source_id=source_id,
        approved_run_id=run_id,
    )

    preview_path = tmp_path / ".local/semantic_tagger_benchmark/human_review_preview.local.jsonl"
    previews = [json.loads(line) for line in preview_path.read_text().splitlines()]
    assert all(item["preview_version"] == HUMAN_REVIEW_PREVIEW_VERSION for item in previews)
    serialized = json.dumps(previews)
    for forbidden in (
        "openai",
        "endpoint",
        "model_name",
        "response_schema",
        "request_body",
        "cloud",
    ):
        assert forbidden not in serialized.casefold()
