import pytest
from scripts.semantic_tagger.schemas import TaggerOutputV3ModelOutput


def test_tagger_output_v3_schema_constraints():
    schema = TaggerOutputV3ModelOutput.model_json_schema()

    # The top level should have definitions for SemanticConceptV3ModelOutput
    defs = schema.get("$defs", {})
    concept_schema = defs.get("SemanticConceptV3ModelOutput", {})

    # Check required fields
    required = concept_schema.get("required", [])
    assert "entity_types" in required, "entity_types must be required"
    assert "domains" in required, "domains must be required"

    # Check constraints on entity_types
    props = concept_schema.get("properties", {})
    entity_types_prop = props.get("entity_types", {})
    assert entity_types_prop.get("type") == "array"
    assert entity_types_prop.get("minItems") == 1
    assert entity_types_prop.get("maxItems") == 3

    # Check constraints on domains
    domains_prop = props.get("domains", {})
    assert domains_prop.get("type") == "array"
    assert domains_prop.get("minItems") == 1
    assert domains_prop.get("maxItems") == 5


def test_validation_errors_for_empty_lists():
    # omitted facet fields fail validation
    with pytest.raises(ValueError) as exc:
        TaggerOutputV3ModelOutput(
            unit_quality="meaningful",
            concepts=[
                {
                    "concept_id": "C1",
                    "surface_label": "test",
                    "preferred_label": "test",
                    "language": "pl",
                    "importance": 1.0,
                    "confidence": 1.0,
                }
            ],
        )
    assert "Field required" in str(exc.value)

    # empty facet fields fail validation
    with pytest.raises(ValueError) as exc:
        TaggerOutputV3ModelOutput(
            unit_quality="meaningful",
            concepts=[
                {
                    "concept_id": "C1",
                    "surface_label": "test",
                    "preferred_label": "test",
                    "language": "pl",
                    "entity_types": [],
                    "domains": [],
                    "importance": 1.0,
                    "confidence": 1.0,
                }
            ],
        )
    assert "List should have at least 1 item" in str(exc.value)
