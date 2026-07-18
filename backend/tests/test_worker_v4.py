import pytest
import sqlite3
import hashlib
from unittest.mock import MagicMock, patch

from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.ollama_client import OllamaGenerationResult
from scripts.semantic_tagger.schemas import TaggerOutputV3ModelOutput


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


def test_persistence_failed_response(store):
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
        worker.run_one(job, DummyUnit())

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        a = conn.execute(
            "SELECT * FROM tagging_attempt WHERE attempt_id=?", (job["attempt_id"],)
        ).fetchone()

        assert a["response_output_text"] == invalid_json
        assert a["response_output_hash"] == hashlib.sha256(invalid_json.encode("utf-8")).hexdigest()
        assert a["response_output_truncated"] == 0
        assert a["response_output_bytes"] == len(invalid_json.encode("utf-8"))
        assert a["response_output_stored_bytes"] == a["response_output_bytes"]


def test_persistence_successful_response(store):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")

    mock_client = MagicMock()
    valid_json = '{"languages": ["en"], "content_types": ["code"], "unit_quality": "meaningful", "concepts": [{"concept_id": "C1", "surface_label": "label", "preferred_label": "label", "language": "en", "evidence": ["E1"], "entity_types": ["person"], "domains": ["general"], "importance": 1, "confidence": 1}], "relations": []}'
    mock_client.generate_tags.return_value = OllamaGenerationResult(valid_json, 100, 10, 20, "stop")

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker = Worker(job_store, mock_client)
        worker.run_one(job, DummyUnit())

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        a = conn.execute(
            "SELECT * FROM tagging_attempt WHERE attempt_id=?", (job["attempt_id"],)
        ).fetchone()

        assert a["response_output_text"] == valid_json
        assert a["response_output_hash"] == hashlib.sha256(valid_json.encode("utf-8")).hexdigest()


def test_persistence_oversized_response(store):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")

    # Generate oversized response
    oversized_json = "x" * (1_048_576 + 100)

    mock_client = MagicMock()
    mock_client.generate_tags.return_value = OllamaGenerationResult(
        oversized_json, 100, 10, 20, "stop"
    )

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker = Worker(job_store, mock_client)
        worker.run_one(job, DummyUnit())

    with sqlite3.connect(job_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        a = conn.execute(
            "SELECT * FROM tagging_attempt WHERE attempt_id=?", (job["attempt_id"],)
        ).fetchone()

        assert a["response_output_truncated"] == 1
        assert a["response_output_bytes"] == len(oversized_json.encode("utf-8"))
        assert a["response_output_stored_bytes"] == 1_048_576
        assert len(a["response_output_text"].encode("utf-8")) == 1_048_576
        assert (
            a["response_output_hash"] == hashlib.sha256(oversized_json.encode("utf-8")).hexdigest()
        )


def test_exact_schema_reaches_ollama(store):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")

    mock_client = MagicMock()
    valid_json = '{"languages": ["en"], "content_types": ["code"], "unit_quality": "meaningful", "concepts": [], "relations": []}'
    mock_client.generate_tags.return_value = OllamaGenerationResult(valid_json, 100, 10, 20, "stop")

    with patch("scripts.semantic_tagger.prompt_builder.build_tagger_prompt") as mock_build:
        mock_build.return_value = MagicMock(
            prompt="prompt", evidence_alias_to_event_id={"E1": "e1"}
        )
        worker = Worker(job_store, mock_client)
        worker.run_one(job, DummyUnit())

    # Assert exact generated schema reaches the format parameter
    expected_schema = TaggerOutputV3ModelOutput.model_json_schema()
    call_args = mock_client.generate_tags.call_args
    assert call_args[0][1] == expected_schema


def test_mixed_validation_error_is_not_facets_missing(store):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")

    mock_client = MagicMock()
    # Mixed error: missing entity_types AND missing importance
    mixed_invalid_json = '{"languages": ["en"], "content_types": ["code"], "unit_quality": "meaningful", "concepts": [{"concept_id": "C1", "surface_label": "label", "preferred_label": "label", "language": "en", "evidence": ["E1"], "domains": ["dom"]}], "relations": []}'
    mock_client.generate_tags.return_value = OllamaGenerationResult(
        mixed_invalid_json, 100, 10, 20, "stop"
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
            "SELECT status, retry_reason, error_code FROM tagging_job WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
        assert j["status"] == "failed"
        assert j["retry_reason"] is None
        assert j["error_code"] == "validation_error"


def test_unrelated_validation_error_is_not_facets_missing(store):
    job_store, run_id = store
    job = job_store.claim_next_job(run_id, "w1")

    mock_client = MagicMock()
    # Unrelated error: missing importance completely
    unrelated_invalid_json = '{"languages": ["en"], "content_types": ["code"], "unit_quality": "meaningful", "concepts": [{"concept_id": "C1", "surface_label": "label", "preferred_label": "label", "language": "en", "evidence": ["E1"], "entity_types": ["t1"], "domains": ["d1"]}], "relations": []}'
    mock_client.generate_tags.return_value = OllamaGenerationResult(
        unrelated_invalid_json, 100, 10, 20, "stop"
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
            "SELECT status, retry_reason, error_code FROM tagging_job WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
        assert j["status"] == "failed"
        assert j["retry_reason"] is None
        assert j["error_code"] == "validation_error"
