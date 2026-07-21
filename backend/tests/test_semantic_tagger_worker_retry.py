import pytest
import sqlite3
from unittest.mock import MagicMock, patch

from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.ollama_client import OllamaGenerationResult


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "semantic_tagger.sqlite3"
    job_store = JobStore(db_path)
    run_id = job_store.create_run(
        {
            "model_name": "test-model",
            "schema_version": "semantic-tags-v3",
            "prompt_version": "semantic-hybrid-v3",
        }
    )
    job_store.save_unit(
        {
            "unit_id": "u1",
            "context_id": "ctx1",
            "sequence_no": 1,
            "content_hash": "hash1",
            "event_ids": ["e1"],
            "segments": [],
            "event_count": 1,
            "character_count": 10,
            "estimated_token_count": 2,
            "first_event_at": "2026-07-01",
            "last_event_at": "2026-07-01",
        }
    )
    job_store.queue_job("key1", run_id, "u1", "hash1")
    return job_store, run_id


class DummyUnit:
    def __init__(self):
        self.contains_code = False
        self.contains_logs = False
        self.contains_urls = False
        self.content = "content"
        self.event_ids = ["e1"]
        self.unit_id = "u1"


def test_first_facets_missing_transitions_to_pending_and_persists_reason(store):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")

    mock_client = MagicMock()
    invalid_json = '{"languages": ["en"], "content_types": ["code"], "unit_quality": "meaningful", "concepts": [{"concept_id": "C1", "surface_label": "label", "preferred_label": "label", "language": "en", "evidence": ["E1"], "entity_types": [], "domains": [], "importance": 1, "confidence": 1}], "relations": []}'
    mock_client.generate_tags.return_value = OllamaGenerationResult(
        invalid_json, 100, 10, 20, "stop"
    )

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker = Worker(job_store, mock_client)
        success = worker.run_one(job, DummyUnit())
        assert not success

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status, retry_reason FROM tagging_job WHERE job_id=?", (job["job_id"],)
        ).fetchone()
        assert j["status"] == "pending"
        assert j["retry_reason"] == "facets_missing"

        a = conn.execute(
            "SELECT status, elapsed_ms, done_reason, error_code FROM tagging_attempt WHERE attempt_id=?",
            (job["attempt_id"],),
        ).fetchone()
        assert a["status"] == "failed"
        assert a["elapsed_ms"] == 20
        assert a["done_reason"] == "stop"
        assert a["error_code"] == "facets_missing"


def test_second_facets_missing_transitions_to_failed(store):
    job_store, run_id = store

    # Attempt 1
    job1 = job_store.claim_next_job(run_id, "w1")
    mock_client = MagicMock()
    invalid_json = '{"languages": ["en"], "content_types": ["code"], "unit_quality": "meaningful", "concepts": [{"concept_id": "C1", "surface_label": "label", "preferred_label": "label", "language": "en", "evidence": ["E1"], "entity_types": [], "domains": [], "importance": 1, "confidence": 1}], "relations": []}'
    mock_client.generate_tags.return_value = OllamaGenerationResult(
        invalid_json, 100, 10, 20, "stop"
    )

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker = Worker(job_store, mock_client)
        worker.run_one(job1, DummyUnit())

    # Attempt 2
    job2 = job_store.claim_next_job(run_id, "w1")
    assert job2["attempt_count"] == 2

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker.run_one(job2, DummyUnit())

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status FROM tagging_job WHERE job_id=?", (job1["job_id"],)
        ).fetchone()
        assert j["status"] == "failed"


def test_non_retry_validation_failure_becomes_failed(store):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")

    mock_client = MagicMock()
    mock_client.generate_tags.return_value = OllamaGenerationResult(
        "{not json}", 100, 10, 20, "stop"
    )

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker = Worker(job_store, mock_client)
        worker.run_one(job, DummyUnit())

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status, error_code FROM tagging_job WHERE job_id=?", (job["job_id"],)
        ).fetchone()
        assert j["status"] == "failed"
        assert j["error_code"] == "invalid_json"


def test_successful_v3_output_becomes_done(
    store,
    isolate_semantic_vocabulary,
    tmp_path,
    monkeypatch,
):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")
    working_directory = tmp_path / "readonly-default-vocabulary"
    default_vocabulary = working_directory / "data/semantic_vocabulary.local.sqlite3"
    default_vocabulary.parent.mkdir(parents=True)
    sentinel = b"read-only synthetic vocabulary sentinel"
    default_vocabulary.write_bytes(sentinel)
    default_vocabulary.chmod(0o444)
    monkeypatch.chdir(working_directory)
    assert not isolate_semantic_vocabulary.exists()

    mock_client = MagicMock()
    valid_json = '{"languages": ["en"], "content_types": ["code"], "unit_quality": "meaningful", "concepts": [{"concept_id": "C1", "surface_label": "label", "preferred_label": "label", "language": "en", "evidence": ["E1"], "entity_types": ["person_name"], "domains": ["computer_science"], "importance": 1, "confidence": 1}], "relations": []}'
    mock_client.generate_tags.return_value = OllamaGenerationResult(valid_json, 100, 10, 20, "stop")

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker = Worker(job_store, mock_client)
        success = worker.run_one(job, DummyUnit())
        assert success

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        j = conn.execute(
            "SELECT status, output_hash FROM tagging_job WHERE job_id=?", (job["job_id"],)
        ).fetchone()
        assert j["status"] == "done"
        assert j["output_hash"] is not None

    assert default_vocabulary.read_bytes() == sentinel
    assert not any(
        default_vocabulary.with_name(default_vocabulary.name + suffix).exists()
        for suffix in ("-wal", "-shm", "-journal")
    )
    with sqlite3.connect(isolate_semantic_vocabulary) as conn:
        occurrences = conn.execute(
            """
            SELECT o.dimension, c.normalized_label, o.unit_id, o.concept_id
            FROM vocabulary_occurrence AS o
            JOIN vocabulary_candidate AS c ON c.candidate_id = o.candidate_id
            ORDER BY o.dimension, c.normalized_label
            """
        ).fetchall()
    assert occurrences == [
        ("domain", "computer_science", "u1", "C1"),
        ("entity_type", "person_name", "u1", "C1"),
    ]
