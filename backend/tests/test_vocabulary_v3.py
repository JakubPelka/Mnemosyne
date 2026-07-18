import sqlite3
import pytest
from scripts.semantic_tagger.vocabulary_store import VocabularyStore
from scripts.semantic_tagger.schemas import (
    SemanticConceptV3Stored,
    SemanticRelationV3Stored,
    TaggerOutputV3Stored,
)


@pytest.fixture
def vocab_store(tmp_path):
    db_path = tmp_path / "semantic_vocabulary.sqlite3"
    return VocabularyStore(db_path=db_path)


def test_v3_job_successful_relations_extraction(vocab_store):
    output = TaggerOutputV3Stored(
        schema_version="semantic-tags-v3",
        languages=["en"],
        content_types=["text"],
        unit_quality="meaningful",
        concepts=[
            SemanticConceptV3Stored(
                concept_id="C1",
                surface_label="test concept",
                preferred_label="test concept",
                language="en",
                entity_types=["test_type"],
                domains=["test_domain"],
                context_roles=["test_role"],
                importance=0.9,
                confidence=0.9,
                evidence_event_ids=["ev1", "ev2"],
            ),
            SemanticConceptV3Stored(
                concept_id="C2",
                surface_label="test concept 2",
                preferred_label="test concept 2",
                language="en",
                entity_types=["test_type_2"],
                domains=["test_domain_2"],
                context_roles=["test_role_2"],
                importance=0.9,
                confidence=0.9,
                evidence_event_ids=["ev3"],
            ),
        ],
        relations=[
            SemanticRelationV3Stored(
                subject_concept_id="C1",
                predicate="related_to",
                object_concept_id="C2",
                confidence=0.8,
                evidence_event_ids=["ev1", "ev2", "ev3"],
            )
        ],
    )

    vocab_store.update_from_tagger_output_v3(output, unit_id="u1", run_id="r1", job_id="j1")

    with sqlite3.connect(vocab_store.db_path) as conn:
        conn.row_factory = sqlite3.Row

        # relation vocabulary occurrences use the stored evidence_event_ids
        occs = conn.execute(
            "SELECT * FROM vocabulary_occurrence WHERE dimension='relation_predicate'"
        ).fetchall()
        assert len(occs) == 1

        # Check label
        labels = conn.execute(
            "SELECT * FROM vocabulary_label WHERE candidate_id=?", (occs[0]["candidate_id"],)
        ).fetchall()
        assert any(lbl["label"] == "related_to" for lbl in labels)


def test_v3_empty_relations_list_remains_valid(vocab_store):
    output = TaggerOutputV3Stored(
        schema_version="semantic-tags-v3",
        languages=["en"],
        content_types=["text"],
        unit_quality="meaningful",
        concepts=[
            SemanticConceptV3Stored(
                concept_id="C1",
                surface_label="test concept",
                preferred_label="test concept",
                language="en",
                entity_types=["test_type"],
                domains=["test_domain"],
                context_roles=["test_role"],
                importance=0.9,
                confidence=0.9,
                evidence_event_ids=["ev1", "ev2"],
            )
        ],
        relations=[],
    )

    vocab_store.update_from_tagger_output_v3(output, unit_id="u2", run_id="r2", job_id="j2")
    with sqlite3.connect(vocab_store.db_path) as conn:
        occs = conn.execute(
            "SELECT * FROM vocabulary_occurrence WHERE dimension='relation_predicate'"
        ).fetchall()
        assert len(occs) == 0


def test_v3_concept_extraction_unchanged(vocab_store):
    output = TaggerOutputV3Stored(
        schema_version="semantic-tags-v3",
        languages=["en"],
        content_types=["text"],
        unit_quality="meaningful",
        concepts=[
            SemanticConceptV3Stored(
                concept_id="C1",
                surface_label="test concept",
                preferred_label="test concept",
                language="en",
                entity_types=["test_type"],
                domains=["test_domain"],
                context_roles=[],
                importance=0.9,
                confidence=0.9,
                evidence_event_ids=["ev1"],
            )
        ],
        relations=[],
    )
    vocab_store.update_from_tagger_output_v3(output, unit_id="u3", run_id="r3", job_id="j3")
    with sqlite3.connect(vocab_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        occs = conn.execute(
            "SELECT * FROM vocabulary_occurrence WHERE dimension='entity_type'"
        ).fetchall()
        assert len(occs) == 1
        labels = conn.execute(
            "SELECT * FROM vocabulary_label WHERE candidate_id=?", (occs[0]["candidate_id"],)
        ).fetchall()
        assert any(lbl["label"] == "test_type" for lbl in labels)
