import pytest
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError
from scripts.semantic_tagger.schemas import (
    TaggerOutput,
    TaggerOutputV3ModelOutput,
    TaggerOutputV3Stored,
)


@pytest.mark.local_ollama
def test_ollama_integration_v2():
    client = OllamaClient("qwen3:14b")

    try:
        version = client.get_version()
        assert version != "unknown"
    except OllamaError:
        pytest.skip("Ollama is not running locally.")

    prompt = "Wyodrębnij projekt Mnemosyne oraz QGIS. Odpowiedz tylko w JSON."
    schema_json = TaggerOutput.model_json_schema()

    gen_res = client.generate_tags(prompt, schema_json, num_predict=4096, seed=42, num_ctx=8192)
    output = gen_res.output_text

    output_obj = TaggerOutput.model_validate_json(output)
    assert output_obj.schema_version == "semantic-tags-v2"

    labels = [c.surface_label.lower() for c in output_obj.concepts]
    assert any("mnemosyne" in lbl for lbl in labels)


@pytest.mark.local_ollama
def test_ollama_integration_v3():
    client = OllamaClient("qwen3:14b")

    try:
        version = client.get_version()
        assert version != "unknown"
    except OllamaError:
        pytest.skip("Ollama is not running locally.")

    prompt = (
        "Przeanalizuj poniższy tekst i wyodrębnij koncepcje oraz relacje. "
        "Tekst: 'Projekt Mnemosyne to aplikacja bazująca na QGIS. Aplikacja została napisana w 2026 roku.' "
        "Odpowiedz tylko w formacie JSON zgodnym ze schematem. "
        "Użyj aliasów E1, E2 dla dowodów."
    )
    schema_json = TaggerOutputV3ModelOutput.model_json_schema()

    gen_res = client.generate_tags(prompt, schema_json, num_predict=4096, seed=42, num_ctx=8192)
    output = gen_res.output_text

    output_obj = TaggerOutputV3ModelOutput.model_validate_json(output)
    assert output_obj.schema_version == "semantic-tags-v3"

    # Check evidence aliases E*
    assert output_obj.concepts
    for c in output_obj.concepts:
        assert c.evidence_aliases
        for alias in c.evidence_aliases:
            assert alias.startswith("E")

    # Check entity_types not empty
    assert any(c.entity_types for c in output_obj.concepts)
    # Check domains not empty
    assert any(c.domains for c in output_obj.concepts)

    # Validate stored round-trip to TaggerOutputV3Stored
    # We create a mapping from alias to real event_id, e.g. E1 -> event1, E2 -> event2
    alias_map = {"E1": "event1", "E2": "event2"}

    stored_concepts = []
    for c in output_obj.concepts:
        real_evidence = [alias_map.get(a, a) for a in c.evidence_aliases]
        stored_concepts.append(
            c.model_dump(exclude={"evidence_aliases"}) | {"evidence_event_ids": real_evidence}
        )

    stored_relations = []
    for r in output_obj.relations:
        real_evidence = [alias_map.get(a, a) for a in r.evidence_aliases]
        stored_relations.append(
            r.model_dump(exclude={"evidence_aliases"}) | {"evidence_event_ids": real_evidence}
        )

    stored_data = {
        "schema_version": output_obj.schema_version,
        "languages": output_obj.languages,
        "content_types": output_obj.content_types,
        "unit_quality": output_obj.unit_quality.model_dump(),
        "concepts": stored_concepts,
        "relations": stored_relations,
    }

    stored_obj = TaggerOutputV3Stored.model_validate(stored_data)
    assert stored_obj.schema_version == "semantic-tags-v3"
    assert len(stored_obj.concepts) == len(output_obj.concepts)

    # Dump and reload JSON for round trip
    json_data = stored_obj.model_dump_json()
    reloaded_obj = TaggerOutputV3Stored.model_validate_json(json_data)
    assert reloaded_obj.schema_version == "semantic-tags-v3"
    assert len(reloaded_obj.concepts) == len(stored_obj.concepts)
