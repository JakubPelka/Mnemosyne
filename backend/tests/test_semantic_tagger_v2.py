import sqlite3
from scripts.semantic_tagger.schemas import TaggerOutput, Concept
from scripts.semantic_tagger.vocabulary_store import VocabularyStore


def test_multidimensional_concept_schema():
    # Test valid parsing
    data = {
        "unit_quality": {"mostly_code": False, "mostly_logs": False, "insufficient_context": False},
        "languages": ["sv"],
        "content_types": ["chat"],
        "concepts": [
            {
                "concept_id": "c1",
                "surface_label": "fönsterruta",
                "preferred_label": "fönsterruta",
                "language": "sv",
                "entity_types": [{"label": "object_part", "scheme": "local", "confidence": 0.96}],
                "domains": [{"label": "vehicles", "scheme": "local", "confidence": 0.91}],
                "context_roles": [
                    {"label": "damage_location", "scheme": "local", "confidence": 0.92}
                ],
                "external_matches": [],
                "importance": 0.82,
                "confidence": 0.91,
                "evidence_event_ids": ["e1"],
            },
            {
                "concept_id": "c2",
                "surface_label": "empty_facets",
                "preferred_label": "empty",
                "language": "sv",
                "entity_types": [],
                "domains": [],
                "context_roles": [],
                "external_matches": [],
                "importance": 0.5,
                "confidence": 0.5,
                "evidence_event_ids": ["e1"],
            },
        ],
        "relations": [
            {
                "subject_concept_id": "c1",
                "predicate": "located_on",
                "object_concept_id": "c2",
                "confidence": 0.86,
                "evidence_event_ids": ["e1"],
            }
        ],
    }

    out = TaggerOutput(**data)
    assert len(out.concepts) == 2
    assert out.concepts[0].entity_types[0].label == "object_part"
    assert len(out.concepts[1].entity_types) == 0
    assert out.relations[0].predicate == "located_on"


def test_schema_limits():
    # Test max items constraints
    from pydantic import ValidationError

    facets = [{"label": f"t{i}", "scheme": "local", "confidence": 0.9} for i in range(10)]

    try:
        Concept(
            concept_id="c1",
            surface_label="lbl",
            preferred_label="lbl",
            language="en",
            entity_types=facets[:6],  # max is 5
            domains=[],
            context_roles=[],
            external_matches=[],
            importance=0.5,
            confidence=0.5,
            evidence_event_ids=[],
        )
        assert False, "Should have failed entity_types limit"
    except ValidationError:
        pass

    try:
        Concept(
            concept_id="c1",
            surface_label="lbl",
            preferred_label="lbl",
            language="en",
            entity_types=[],
            domains=facets[:9],  # max is 8
            context_roles=[],
            external_matches=[],
            importance=0.5,
            confidence=0.5,
            evidence_event_ids=[],
        )
        assert False, "Should have failed domains limit"
    except ValidationError:
        pass


def test_vocabulary_store(tmp_path):
    db_path = tmp_path / "vocab.sqlite3"
    store = VocabularyStore(db_path)

    # 1. New concept
    store.upsert_concept("entity_type", "Fönsterruta", 0.9, "sv")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.execute("SELECT * FROM vocabulary_candidate").fetchone()
        assert c["dimension"] == "entity_type"
        assert c["normalized_label"] == "fönsterruta"  # lowercased
        assert c["preferred_label"] == "Fönsterruta"
        assert c["occurrence_count"] == 1
        assert c["status"] == "local_candidate"

        labels = conn.execute("SELECT label, label_kind FROM vocabulary_label").fetchall()
        assert len(labels) == 2  # preferred and original_surface

    # 2. Existing concept, increase count and confidence
    store.upsert_concept("entity_type", " fönsterruta ", 0.95, "sv")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.execute("SELECT * FROM vocabulary_candidate").fetchone()
        assert c["occurrence_count"] == 2
        assert c["max_confidence"] == 0.95

    # 3. New original surface, same concept
    store.upsert_concept("entity_type", "FÖNSTERRUTA", 0.8, "sv")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.execute("SELECT * FROM vocabulary_candidate").fetchone()
        assert c["occurrence_count"] == 3
        assert c["max_confidence"] == 0.95  # didn't decrease

        labels = conn.execute(
            "SELECT label, label_kind FROM vocabulary_label WHERE label_kind='original_surface'"
        ).fetchall()
        surfaces = [r["label"] for r in labels]
        assert "Fönsterruta" in surfaces
        assert " fönsterruta " in surfaces
        assert "FÖNSTERRUTA" in surfaces
