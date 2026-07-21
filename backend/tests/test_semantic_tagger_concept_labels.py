import json
import re
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from scripts.semantic_tagger.ollama_client import OllamaGenerationResult
from scripts.semantic_tagger.schemas import (
    CONCEPT_LABEL_PATTERN,
    SemanticRelationV3ModelOutput,
    SemanticRelationV3Stored,
    TaggerOutputV3ModelOutput,
    TaggerOutputV3Stored,
)
from scripts.semantic_tagger.vocabulary_store import (
    V3_CONCEPT_LABEL_DIMENSIONS,
    VocabularyStore,
    validate_vocabulary_label,
)
from scripts.semantic_tagger.worker import Worker


PREVIOUSLY_VALID_LABELS = (
    "concept",
    "concept_2",
    "a",
    "a_",
    "concept__part",
)
DIGIT_PREFIXED_LABELS = (
    "3d",
    "3d_modeling",
    "3d_visualization",
    "3d_model_element",
    "2fa",
    "4k_video",
    "5g_network",
)
INVALID_LABELS = (
    "",
    "_concept",
    "-concept",
    "Concept",
    "concept-name",
    "concept name",
    "3",
    "123",
    "___",
    "/concept",
    "café",
)
ALL_LABEL_CASES = (
    *((label, True) for label in PREVIOUSLY_VALID_LABELS),
    *((label, True) for label in DIGIT_PREFIXED_LABELS),
    *((label, False) for label in INVALID_LABELS),
)
FACET_FIELDS = ("entity_types", "domains", "context_roles")


def _model_payload(label: str, field: str = "domains") -> dict:
    concept = {
        "concept_id": "C1",
        "surface_label": "synthetic label",
        "preferred_label": "synthetic label",
        "language": "en",
        "entity_types": ["concept"],
        "domains": ["concept"],
        "context_roles": ["concept"],
        "importance": 1.0,
        "confidence": 1.0,
        "evidence": ["E1"],
    }
    concept[field] = [label]
    return {
        "schema_version": "semantic-tags-v3",
        "languages": ["en"],
        "content_types": ["text"],
        "unit_quality": "meaningful",
        "concepts": [concept],
        "relations": [],
    }


def _runtime_accepts(label: str, field: str) -> bool:
    try:
        TaggerOutputV3ModelOutput.model_validate(_model_payload(label, field))
    except ValidationError:
        return False
    return True


def _vocabulary_accepts(label: str, dimension: str) -> bool:
    try:
        validate_vocabulary_label(dimension, label)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(("label", "expected"), ALL_LABEL_CASES)
def test_runtime_json_schema_and_vocabulary_label_contract_agree(label, expected):
    schema = TaggerOutputV3ModelOutput.model_json_schema()
    properties = schema["$defs"]["SemanticConceptV3ModelOutput"]["properties"]

    for field in FACET_FIELDS:
        exported_pattern = properties[field]["items"]["pattern"]
        assert exported_pattern == CONCEPT_LABEL_PATTERN
        assert (re.fullmatch(exported_pattern, label, flags=re.ASCII) is not None) is expected
        assert _runtime_accepts(label, field) is expected

    for dimension in V3_CONCEPT_LABEL_DIMENSIONS:
        assert _vocabulary_accepts(label, dimension) is expected


def test_complete_output_with_historical_3d_labels_is_valid():
    labels = ("3d_modeling", "3d_visualization", "3d_model_element")
    output = TaggerOutputV3ModelOutput.model_validate(
        {
            "schema_version": "semantic-tags-v3",
            "languages": ["en"],
            "content_types": ["technical"],
            "unit_quality": "meaningful",
            "concepts": [
                {
                    "concept_id": f"C{index}",
                    "surface_label": label,
                    "preferred_label": label,
                    "language": "en",
                    "entity_types": [label],
                    "domains": [label],
                    "context_roles": ["subject"],
                    "importance": 0.9,
                    "confidence": 0.9,
                    "evidence": ["E1"],
                }
                for index, label in enumerate(labels, start=1)
            ],
            "relations": [
                {
                    "subject_concept_id": "C1",
                    "predicate": "related_to",
                    "object_concept_id": "C2",
                    "confidence": 0.8,
                    "evidence": ["E1"],
                },
                {
                    "subject_concept_id": "C2",
                    "predicate": "part_of",
                    "object_concept_id": "C3",
                    "confidence": 0.8,
                    "evidence": ["E1"],
                },
            ],
        }
    )

    assert [concept.preferred_label for concept in output.concepts] == list(labels)


def test_stored_output_uses_the_same_concept_label_contract():
    payload = _model_payload("3d_modeling")
    payload["concepts"][0]["evidence_event_ids"] = ["event-1"]
    payload["concepts"][0].pop("evidence")

    stored = TaggerOutputV3Stored.model_validate(payload)
    assert stored.concepts[0].domains == ["3d_modeling"]

    payload["concepts"][0]["domains"] = ["123"]
    with pytest.raises(ValidationError):
        TaggerOutputV3Stored.model_validate(payload)


def test_vocabulary_store_revalidates_labels_before_normalization(tmp_path):
    store = VocabularyStore(tmp_path / "vocabulary.sqlite3")
    common = {
        "language": "en",
        "confidence": 0.9,
        "run_id": "run-1",
        "job_id": "job-1",
        "unit_id": "unit-1",
        "concept_id": "C1",
    }

    store.upsert_occurrence_v3("domain", "3d_modeling", **common)
    with pytest.raises(ValueError):
        store.upsert_occurrence_v3("domain", " 3d_modeling ", **common)

    with store._connect() as connection:
        labels = connection.execute(
            "SELECT preferred_label FROM vocabulary_candidate ORDER BY preferred_label"
        ).fetchall()
    assert [row[0] for row in labels] == ["3d_modeling"]


def test_predicate_contract_is_unchanged():
    expected_pattern = r"^[a-z][a-z0-9_]*$"
    output_schema = SemanticRelationV3ModelOutput.model_json_schema()
    stored_schema = SemanticRelationV3Stored.model_json_schema()
    assert output_schema["properties"]["predicate"]["pattern"] == expected_pattern
    assert stored_schema["properties"]["predicate"]["pattern"] == expected_pattern

    SemanticRelationV3ModelOutput(
        subject_concept_id="C1",
        predicate="related_to",
        object_concept_id="C2",
        confidence=1.0,
    )
    with pytest.raises(ValidationError):
        SemanticRelationV3ModelOutput(
            subject_concept_id="C1",
            predicate="3d_related_to",
            object_concept_id="C2",
            confidence=1.0,
        )


class _RecordingStore:
    def __init__(self):
        self.error_code = None

    def record_attempt_response_metadata(self, *args, **kwargs):
        return None

    def fail_job(
        self,
        job_id,
        attempt_id,
        lease_token,
        error_code,
        error_message,
        done_reason,
        retry_reason=None,
    ):
        self.error_code = error_code


class _SyntheticUnit:
    content = "synthetic input"
    event_ids = ["event-1"]
    unit_id = "unit-1"


def test_malformed_concept_label_remains_a_validation_error():
    response = _model_payload("123")
    client = MagicMock()
    client.generate_tags.return_value = OllamaGenerationResult(
        json.dumps(response), 10, 10, 20, "stop"
    )
    store = _RecordingStore()
    job = {
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "lease_token": "lease-1",
        "attempt_count": 1,
        "prompt_version": "semantic-hybrid-v3",
        "schema_version": "semantic-tags-v3",
        "settings": {"num_predict": 1536, "seed": 42, "num_ctx": 8192},
    }

    with patch("scripts.semantic_tagger.worker.build_worker_final_prompt") as build_prompt:
        build_prompt.return_value = MagicMock(
            prompt="synthetic prompt",
            evidence_alias_to_event_id={"E1": "event-1"},
        )
        succeeded = Worker(store, client).run_one(job, _SyntheticUnit())

    assert not succeeded
    assert store.error_code == "validation_error"
