import pytest
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError
from scripts.semantic_tagger.schemas import TaggerOutput


@pytest.mark.local_ollama
def test_ollama_integration():
    client = OllamaClient("qwen3:14b")

    # Check if we can connect
    try:
        version = client.get_version()
        assert version != "unknown"
    except OllamaError:
        pytest.skip("Ollama is not running locally.")

    prompt = "Wyodrębnij projekt Mnemosyne oraz QGIS. Odpowiedz tylko w JSON."
    schema_json = TaggerOutput.model_json_schema()

    output, p_tok, c_tok = client.generate_tags(prompt, schema_json)

    assert isinstance(output, TaggerOutput)
    assert output.schema_version == "semantic-tags-v1"

    labels = [c.label.lower() for c in output.concepts]
    assert any("mnemosyne" in l for l in labels)
