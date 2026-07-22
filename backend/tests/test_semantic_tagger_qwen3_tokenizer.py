import hashlib
import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.semantic_tagger.prompt_budget import (
    PromptBudgetError,
    estimate_prompt_tokens,
)
from scripts.semantic_tagger.qwen3_tokenizer import (
    EXACT_PROMPT_ESTIMATOR_VERSION,
    FRAMING_CONTRACT_VERSION,
    PINNED_OLLAMA_VERSION,
    SUPPORTED_REQUEST_API_MODE,
    ExactTokenizerContractError,
    ExactTokenizerPins,
    GGUFMetadataError,
    Qwen3GGUFTokenizer,
    count_visible_prompt_tokens,
    estimate_server_prompt_tokens,
    read_gguf_tokenizer_metadata,
    resolve_exact_tokenizer_contract,
    resolve_ollama_models_root,
)


def _independent_bytes_to_unicode() -> dict[int, str]:
    byte_values = list(range(ord("!"), ord("~") + 1))
    byte_values += list(range(ord("¡"), ord("¬") + 1))
    byte_values += list(range(ord("®"), ord("ÿ") + 1))
    unicode_values = list(byte_values)
    extra = 0
    for value in range(256):
        if value not in byte_values:
            byte_values.append(value)
            unicode_values.append(256 + extra)
            extra += 1
    return dict(zip(byte_values, map(chr, unicode_values), strict=True))


def _synthetic_tokenizer_values(
    *,
    tokenizer_model: str = "gpt2",
    tokenizer_pre: str = "qwen2",
    add_bos_token: bool = False,
    merges: list[str] | None = None,
    mismatched_token_types: bool = False,
) -> dict[str, object]:
    byte_encoder = _independent_bytes_to_unicode()
    base_tokens = [byte_encoder[value] for value in range(256)]
    merge_values = (
        merges
        if merges is not None
        else [
            "h e",
            "he l",
            "hel l",
            "hell o",
            f"{byte_encoder[32]} hello",
        ]
    )
    merged_tokens = list(dict.fromkeys(value.replace(" ", "") for value in merge_values))
    special_tokens = ["<|im_start|>", "<|im_end|>", "<think>", "</think>"]
    tokens = base_tokens + merged_tokens + special_tokens
    token_types = [1] * (len(base_tokens) + len(merged_tokens)) + [3, 3, 4, 4]
    if mismatched_token_types:
        token_types.pop()
    bos_id = len(base_tokens) + len(merged_tokens)
    eos_id = bos_id + 1
    return {
        "general.architecture": "qwen3",
        "general.name": "Synthetic Qwen3",
        "tokenizer.chat_template": "synthetic-template-v1",
        "tokenizer.ggml.model": tokenizer_model,
        "tokenizer.ggml.pre": tokenizer_pre,
        "tokenizer.ggml.tokens": tokens,
        "tokenizer.ggml.merges": merge_values,
        "tokenizer.ggml.token_type": token_types,
        "tokenizer.ggml.add_bos_token": add_bos_token,
        "tokenizer.ggml.bos_token_id": bos_id,
        "tokenizer.ggml.eos_token_id": eos_id,
        "tokenizer.ggml.padding_token_id": bos_id,
    }


def _pack_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _pack_value(value: object) -> tuple[int, bytes]:
    if isinstance(value, bool):
        return 7, struct.pack("<?", value)
    if isinstance(value, int):
        return 4, struct.pack("<I", value)
    if isinstance(value, str):
        return 8, _pack_string(value)
    if isinstance(value, list):
        if not value:
            element_type = 8
            payload = b""
        elif all(isinstance(item, str) for item in value):
            element_type = 8
            payload = b"".join(_pack_string(item) for item in value)
        elif all(isinstance(item, int) for item in value):
            element_type = 4
            payload = b"".join(struct.pack("<I", item) for item in value)
        else:
            raise TypeError("Unsupported synthetic GGUF array")
        return 9, struct.pack("<IQ", element_type, len(value)) + payload
    raise TypeError(f"Unsupported synthetic GGUF value: {type(value).__name__}")


def _write_gguf(path: Path, values: dict[str, object]) -> None:
    metadata = []
    for key, value in values.items():
        value_type, payload = _pack_value(value)
        metadata.append(_pack_string(key) + struct.pack("<I", value_type) + payload)
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, len(metadata)) + b"".join(metadata))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_synthetic_model_store(
    tmp_path: Path,
    *,
    values: dict[str, object] | None = None,
) -> tuple[Path, ExactTokenizerPins, Path, Path]:
    root = tmp_path / "models"
    blobs = root / "blobs"
    manifest_path = root / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / "14b"
    blobs.mkdir(parents=True)
    manifest_path.parent.mkdir(parents=True)

    temporary_model = tmp_path / "synthetic.gguf"
    _write_gguf(temporary_model, values or _synthetic_tokenizer_values())
    model_sha = _sha256(temporary_model)
    model_blob = blobs / f"sha256-{model_sha}"
    temporary_model.rename(model_blob)
    template_bytes = b"synthetic Ollama template\n"
    template_sha = hashlib.sha256(template_bytes).hexdigest()
    template_blob = blobs / f"sha256-{template_sha}"
    template_blob.write_bytes(template_bytes)

    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "layers": [
            {
                "mediaType": "application/vnd.ollama.image.model",
                "digest": f"sha256:{model_sha}",
                "size": model_blob.stat().st_size,
            },
            {
                "mediaType": "application/vnd.ollama.image.template",
                "digest": f"sha256:{template_sha}",
                "size": template_blob.stat().st_size,
            },
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    metadata = read_gguf_tokenizer_metadata(model_blob)
    pins = ExactTokenizerPins(
        pin_set_id=f"synthetic-{model_sha[:12]}",
        model_name="qwen3:14b",
        model_manifest_sha256=_sha256(manifest_path),
        model_blob_digest=f"sha256:{model_sha}",
        model_blob_size=model_blob.stat().st_size,
        ollama_template_sha256=template_sha,
        tokenizer_model=metadata.tokenizer_model,
        tokenizer_pre=metadata.tokenizer_pre,
        token_count=len(metadata.tokens),
        merge_count=len(metadata.merges),
        token_array_sha256=metadata.token_array_sha256,
        merge_array_sha256=metadata.merge_array_sha256,
        token_type_array_sha256=metadata.token_type_array_sha256,
        special_token_metadata_sha256=metadata.special_token_metadata_sha256,
        tokenizer_metadata_sha256=metadata.tokenizer_metadata_sha256,
        gguf_chat_template_sha256=metadata.gguf_chat_template_sha256,
        add_bos_token=metadata.add_bos_token,
        bos_token_id=metadata.bos_token_id,
        eos_token_id=metadata.eos_token_id,
        padding_token_id=metadata.padding_token_id,
        request_api_mode=SUPPORTED_REQUEST_API_MODE,
        framing_contract_version=FRAMING_CONTRACT_VERSION,
        framing_token_count=10,
        ollama_version=PINNED_OLLAMA_VERSION,
    )
    return root, pins, model_blob, template_blob


def test_tiny_synthetic_gguf_metadata_and_exact_count_vectors(tmp_path):
    root, pins, model_blob, _ = _write_synthetic_model_store(tmp_path)
    metadata = read_gguf_tokenizer_metadata(model_blob)
    contract = resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)

    assert metadata.gguf_version == 3
    assert metadata.tokenizer_model == "gpt2"
    assert metadata.tokenizer_pre == "qwen2"
    assert metadata.add_bos_token is False
    assert count_visible_prompt_tokens("hello", contract) == 1
    assert count_visible_prompt_tokens("hello hello", contract) == 2
    assert count_visible_prompt_tokens("zażółć", contract) == 10
    assert count_visible_prompt_tokens("🙂", contract) == 4
    assert count_visible_prompt_tokens("<|im_start|>hello", contract) == 2
    assert count_visible_prompt_tokens("<think>hello</think>", contract) == 3
    assert estimate_server_prompt_tokens("hello", contract) == 11


@pytest.mark.parametrize(
    ("text", "expected_visible_count"),
    [
        ("1.\na\n2.\nb\n", 10),
        ("123 456 789", 11),
        ("x" * 257, 257),
        ("ERROR code=500\nhttps://example.invalid/a?b=2", 44),
        ("def f():\n\treturn 4", 18),
    ],
)
def test_synthetic_structural_exact_count_vectors(tmp_path, text, expected_visible_count):
    root, pins, _, _ = _write_synthetic_model_store(tmp_path)
    contract = resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)
    assert count_visible_prompt_tokens(text, contract) == expected_visible_count


def test_no_implicit_bos_and_deterministic_repeated_calls(tmp_path):
    root, pins, _, _ = _write_synthetic_model_store(tmp_path)
    contract = resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)
    first = count_visible_prompt_tokens("hello", contract)
    second = count_visible_prompt_tokens("hello", contract)
    assert first == second == 1
    assert contract.add_bos_token is False


def test_gguf_rejects_malformed_truncated_and_mismatched_arrays(tmp_path):
    truncated = tmp_path / "truncated.gguf"
    truncated.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + b"\x01")
    with pytest.raises(GGUFMetadataError, match="Truncated"):
        read_gguf_tokenizer_metadata(truncated)

    mismatched = tmp_path / "mismatched.gguf"
    _write_gguf(
        mismatched,
        _synthetic_tokenizer_values(mismatched_token_types=True),
    )
    with pytest.raises(GGUFMetadataError, match="lengths do not match"):
        read_gguf_tokenizer_metadata(mismatched)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tokenizer_model", "sentencepiece", "Unsupported tokenizer model"),
        ("tokenizer_pre", "llama-bpe", "Unsupported tokenizer pre-tokenizer"),
        ("add_bos_token", True, "implicit BOS"),
    ],
)
def test_tokenizer_rejects_unsupported_metadata(tmp_path, field, value, message):
    options = {field: value}
    root, pins, model_blob, _ = _write_synthetic_model_store(
        tmp_path,
        values=_synthetic_tokenizer_values(**options),
    )
    metadata = read_gguf_tokenizer_metadata(model_blob)
    with pytest.raises(ExactTokenizerContractError, match=message):
        Qwen3GGUFTokenizer(metadata)
    # The resolver itself still verifies the fixture's deliberately pinned
    # metadata before the tokenizer capability check happens at count time.
    contract = resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)
    with pytest.raises(ExactTokenizerContractError, match=message):
        count_visible_prompt_tokens("x", contract)


def test_model_store_resolution_honours_ollama_models_and_user_default(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    monkeypatch.setenv("OLLAMA_MODELS", str(configured))
    assert resolve_ollama_models_root() == configured.resolve()
    monkeypatch.delenv("OLLAMA_MODELS")
    assert resolve_ollama_models_root(home=tmp_path) == (tmp_path / ".ollama/models").resolve()


def test_model_manifest_resolution_through_ollama_models(tmp_path, monkeypatch):
    root, pins, _, _ = _write_synthetic_model_store(tmp_path)
    monkeypatch.setenv("OLLAMA_MODELS", str(root))
    contract = resolve_exact_tokenizer_contract("qwen3:14b", pins=pins)
    assert contract.requested_model_name == "qwen3:14b"
    assert contract.as_manifest()["tokenizer_token_array_sha256"] == (pins.token_array_sha256)


def test_missing_manifest_blob_and_unsupported_model_fail_closed(tmp_path):
    with pytest.raises(ExactTokenizerContractError, match="supports only"):
        resolve_exact_tokenizer_contract("other:latest", models_root=tmp_path)
    with pytest.raises(ExactTokenizerContractError, match="Missing Ollama model manifest"):
        resolve_exact_tokenizer_contract("qwen3:14b", models_root=tmp_path)

    root, pins, model_blob, _ = _write_synthetic_model_store(tmp_path / "missing")
    model_blob.unlink()
    with pytest.raises(ExactTokenizerContractError, match="Missing Ollama model blob"):
        resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)


def test_model_blob_manifest_digest_mismatch_fails_closed(tmp_path):
    root, pins, model_blob, _ = _write_synthetic_model_store(tmp_path)
    value = bytearray(model_blob.read_bytes())
    value[-1] ^= 1
    model_blob.write_bytes(value)
    with pytest.raises(ExactTokenizerContractError, match="content SHA-256"):
        resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)


def test_template_digest_and_manifest_drift_fail_closed(tmp_path):
    root, pins, _, template_blob = _write_synthetic_model_store(tmp_path)
    template_blob.write_bytes(b"X" * template_blob.stat().st_size)
    with pytest.raises(ExactTokenizerContractError, match="template content SHA-256"):
        resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)

    other_root, other_pins, _, _ = _write_synthetic_model_store(tmp_path / "manifest")
    manifest_path = other_root / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / "14b"
    manifest_path.write_text(manifest_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ExactTokenizerContractError, match="manifest SHA-256"):
        resolve_exact_tokenizer_contract("qwen3:14b", models_root=other_root, pins=other_pins)


def test_merge_array_and_framing_contract_drift_fail_closed(tmp_path):
    root, pins, _, _ = _write_synthetic_model_store(tmp_path)
    with pytest.raises(ExactTokenizerContractError, match="token count"):
        resolve_exact_tokenizer_contract(
            "qwen3:14b",
            models_root=root,
            pins=replace(pins, token_count=pins.token_count + 1),
        )
    with pytest.raises(ExactTokenizerContractError, match="merge count"):
        resolve_exact_tokenizer_contract(
            "qwen3:14b",
            models_root=root,
            pins=replace(pins, merge_count=pins.merge_count + 1),
        )

    contract = resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)
    with pytest.raises(ExactTokenizerContractError, match="Unsupported request API mode"):
        estimate_server_prompt_tokens("hello", contract, request_api_mode="ollama-chat")
    stale = replace(contract, framing_contract_version="")
    with pytest.raises(ExactTokenizerContractError, match="framing metadata"):
        estimate_server_prompt_tokens("hello", stale)


def test_exact_estimator_never_silently_falls_back_to_v2(tmp_path):
    with pytest.raises(PromptBudgetError, match="requires"):
        estimate_prompt_tokens("synthetic", EXACT_PROMPT_ESTIMATOR_VERSION)
    root, pins, _, _ = _write_synthetic_model_store(tmp_path)
    contract = resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)
    assert estimate_prompt_tokens("hello", EXACT_PROMPT_ESTIMATOR_VERSION, contract) == 11


def test_tokenizer_cache_isolated_by_contract_digests(tmp_path):
    root, pins, model_blob, _ = _write_synthetic_model_store(tmp_path)
    contract = resolve_exact_tokenizer_contract("qwen3:14b", models_root=root, pins=pins)
    assert count_visible_prompt_tokens("hello", contract) == 1

    values = _synthetic_tokenizer_values(merges=[])
    _write_gguf(model_blob, values)
    metadata = read_gguf_tokenizer_metadata(model_blob)
    changed = replace(
        contract,
        model_blob_sha256=_sha256(model_blob),
        tokenizer_metadata_sha256=metadata.tokenizer_metadata_sha256,
        tokenizer_token_array_sha256=metadata.token_array_sha256,
        tokenizer_merge_array_sha256=metadata.merge_array_sha256,
        tokenizer_token_type_array_sha256=metadata.token_type_array_sha256,
        special_token_metadata_sha256=metadata.special_token_metadata_sha256,
    )
    assert changed.cache_key != contract.cache_key
    assert count_visible_prompt_tokens("hello", changed) == 5
