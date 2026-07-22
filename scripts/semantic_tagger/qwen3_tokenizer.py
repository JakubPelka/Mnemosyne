"""Pinned, read-only Qwen3 GGUF tokenizer support for prompt budgeting.

This module deliberately implements only the tokenizer contract used by the
locally pinned ``qwen3:14b`` Ollama model.  It never invokes Ollama, loads model
weights, downloads assets, or guesses when any verified contract element
drifts.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO


EXACT_PROMPT_ESTIMATOR_VERSION = "prompt-estimator-v3-qwen3-gguf-exact-plus-10"
SUPPORTED_MODEL_NAME = "qwen3:14b"
SUPPORTED_REQUEST_API_MODE = "ollama-generate"
FRAMING_CONTRACT_VERSION = "ollama-generate-qwen3-v1-ollama-0.30.10"
PINNED_OLLAMA_VERSION = "0.30.10"

_MODEL_MEDIA_TYPE = "application/vnd.ollama.image.model"
_TEMPLATE_MEDIA_TYPE = "application/vnd.ollama.image.template"
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_METADATA_STRING_BYTES = 128 * 1_048_576
_MAX_METADATA_ARRAY_ITEMS = 2_000_000
_GGUF_SELECTED_KEYS = frozenset(
    {
        "general.architecture",
        "general.name",
        "tokenizer.chat_template",
        "tokenizer.ggml.model",
        "tokenizer.ggml.pre",
        "tokenizer.ggml.tokens",
        "tokenizer.ggml.merges",
        "tokenizer.ggml.token_type",
        "tokenizer.ggml.add_bos_token",
        "tokenizer.ggml.bos_token_id",
        "tokenizer.ggml.eos_token_id",
        "tokenizer.ggml.padding_token_id",
    }
)
_SCALAR_FORMATS = {
    0: "B",  # uint8
    1: "b",  # int8
    2: "H",  # uint16
    3: "h",  # int16
    4: "I",  # uint32
    5: "i",  # int32
    6: "f",  # float32
    7: "?",  # bool
    10: "Q",  # uint64
    11: "q",  # int64
    12: "d",  # float64
}
_SPECIAL_TOKEN_TYPES = frozenset({3, 4})  # control and user-defined


class ExactTokenizerContractError(ValueError):
    """The local model/tokenizer/framing contract is absent or has drifted."""


class GGUFMetadataError(ExactTokenizerContractError):
    """GGUF metadata is malformed, truncated, or unsupported."""


@dataclass(frozen=True)
class ExactTokenizerPins:
    """Expected immutable identifiers for one supported local model contract."""

    pin_set_id: str
    model_name: str
    model_manifest_sha256: str
    model_blob_digest: str
    model_blob_size: int
    ollama_template_sha256: str
    tokenizer_model: str
    tokenizer_pre: str
    token_count: int
    merge_count: int
    token_array_sha256: str
    merge_array_sha256: str
    token_type_array_sha256: str
    special_token_metadata_sha256: str
    tokenizer_metadata_sha256: str
    gguf_chat_template_sha256: str
    add_bos_token: bool
    bos_token_id: int
    eos_token_id: int
    padding_token_id: int
    request_api_mode: str
    framing_contract_version: str
    framing_token_count: int
    ollama_version: str


# ``tokenizer_metadata_sha256`` is a canonical digest of the independently
# pinned fields above.  It is filled with the verified value after parsing the
# local GGUF once during implementation; all other values came from the
# reviewed forensic report and are rechecked from disk at runtime.
PINNED_QWEN3_14B_PINS = ExactTokenizerPins(
    pin_set_id="qwen3-14b-a8cc1361-ollama-0.30.10-v1",
    model_name=SUPPORTED_MODEL_NAME,
    model_manifest_sha256=("bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"),
    model_blob_digest=("sha256:a8cc1361f3145dc01f6d77c6c82c9116b9ffe3c97b34716fe20418455876c40e"),
    model_blob_size=9_276_184_896,
    ollama_template_sha256=("ae370d884f108d16e7cc8fd5259ebc5773a0afa6e078b11f4ed7e39a27e0dfc4"),
    tokenizer_model="gpt2",
    tokenizer_pre="qwen2",
    token_count=151_936,
    merge_count=151_387,
    token_array_sha256=("adab67cd4451390df3c68d6b3e74f5095d857769780735c7e7044e571db2a89e"),
    merge_array_sha256=("907981a313a6e78ef2223229042674a39cd4995ee4c4ed40a31b58754e82c26c"),
    token_type_array_sha256=("8f418a40608652b4c98f15fbe97bcc2b3a2ba763ebf647542c3cb7ff00c62589"),
    special_token_metadata_sha256=(
        "0810c46fd6cfb7e7828cce0628d991e9f8b96a0a2e577d1c6481f633e400964c"
    ),
    tokenizer_metadata_sha256=("cd524d96f7ef5530b4358f56c6aba59990752fc5cba72a2599daa038454accfa"),
    gguf_chat_template_sha256=("87a2728cb8dc9fe424d624542f6060ec05a1d285ebbec578bb078900e33396b5"),
    add_bos_token=False,
    bos_token_id=151_643,
    eos_token_id=151_645,
    padding_token_id=151_643,
    request_api_mode=SUPPORTED_REQUEST_API_MODE,
    framing_contract_version=FRAMING_CONTRACT_VERSION,
    framing_token_count=10,
    ollama_version=PINNED_OLLAMA_VERSION,
)


@dataclass(frozen=True)
class GGUFTokenizerMetadata:
    gguf_version: int
    architecture: str
    model_name: str
    tokenizer_model: str
    tokenizer_pre: str
    tokens: tuple[str, ...]
    merges: tuple[str, ...]
    token_types: tuple[int, ...]
    add_bos_token: bool
    bos_token_id: int
    eos_token_id: int
    padding_token_id: int
    gguf_chat_template_sha256: str
    token_array_sha256: str
    merge_array_sha256: str
    token_type_array_sha256: str
    special_token_metadata_sha256: str
    tokenizer_metadata_sha256: str


@dataclass(frozen=True)
class ExactTokenizerContract:
    """Resolved, verified exact-estimator contract and its read-only model path."""

    estimator_version: str
    requested_model_name: str
    pin_set_id: str
    model_manifest_sha256: str
    model_blob_digest: str
    model_blob_sha256: str
    model_blob_size: int
    ollama_template_sha256: str
    tokenizer_model: str
    tokenizer_pre: str
    token_count: int
    merge_count: int
    tokenizer_metadata_sha256: str
    tokenizer_token_array_sha256: str
    tokenizer_merge_array_sha256: str
    tokenizer_token_type_array_sha256: str
    special_token_metadata_sha256: str
    gguf_chat_template_sha256: str
    add_bos_token: bool
    bos_token_id: int
    eos_token_id: int
    padding_token_id: int
    request_api_mode: str
    framing_contract_version: str
    framing_token_count: int
    ollama_version: str
    model_blob_path: Path

    @property
    def cache_key(self) -> str:
        values = (
            self.model_manifest_sha256,
            self.model_blob_sha256,
            self.ollama_template_sha256,
            self.tokenizer_metadata_sha256,
            self.tokenizer_token_array_sha256,
            self.tokenizer_merge_array_sha256,
            self.tokenizer_token_type_array_sha256,
            self.special_token_metadata_sha256,
            self.gguf_chat_template_sha256,
            self.request_api_mode,
            self.framing_contract_version,
            str(self.framing_token_count),
            self.ollama_version,
        )
        return hashlib.sha256("|".join(values).encode("ascii")).hexdigest()

    def as_manifest(self) -> dict[str, Any]:
        """Return path-free identifiers safe to persist in units and run settings."""
        return {
            "estimator_version": self.estimator_version,
            "requested_model_name": self.requested_model_name,
            "pin_set_id": self.pin_set_id,
            "model_manifest_sha256": self.model_manifest_sha256,
            "model_blob_digest": self.model_blob_digest,
            "model_blob_sha256": self.model_blob_sha256,
            "model_blob_size": self.model_blob_size,
            "ollama_template_sha256": self.ollama_template_sha256,
            "tokenizer_model": self.tokenizer_model,
            "tokenizer_pre": self.tokenizer_pre,
            "token_count": self.token_count,
            "merge_count": self.merge_count,
            "tokenizer_metadata_sha256": self.tokenizer_metadata_sha256,
            "tokenizer_token_array_sha256": self.tokenizer_token_array_sha256,
            "tokenizer_merge_array_sha256": self.tokenizer_merge_array_sha256,
            "tokenizer_token_type_array_sha256": self.tokenizer_token_type_array_sha256,
            "special_token_metadata_sha256": self.special_token_metadata_sha256,
            "gguf_chat_template_sha256": self.gguf_chat_template_sha256,
            "add_bos_token": self.add_bos_token,
            "bos_token_id": self.bos_token_id,
            "eos_token_id": self.eos_token_id,
            "padding_token_id": self.padding_token_id,
            "request_api_mode": self.request_api_mode,
            "framing_contract_version": self.framing_contract_version,
            "framing_token_count": self.framing_token_count,
            "ollama_version": self.ollama_version,
        }


def _read_exact(handle: BinaryIO, length: int) -> bytes:
    if length < 0:
        raise GGUFMetadataError("Negative GGUF read length")
    value = handle.read(length)
    if len(value) != length:
        raise GGUFMetadataError("Truncated GGUF metadata")
    return value


def _unpack(handle: BinaryIO, value_format: str) -> Any:
    full_format = "<" + value_format
    return struct.unpack(full_format, _read_exact(handle, struct.calcsize(full_format)))[0]


def _read_string_bytes(handle: BinaryIO) -> bytes:
    length = _unpack(handle, "Q")
    if length > _MAX_METADATA_STRING_BYTES:
        raise GGUFMetadataError(f"GGUF metadata string is too large: {length} bytes")
    return _read_exact(handle, length)


def _decode_string(value: bytes, *, field: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GGUFMetadataError(f"GGUF field {field} is not valid UTF-8") from error


def _skip_exact(handle: BinaryIO, length: int) -> None:
    if length < 0:
        raise GGUFMetadataError("Negative GGUF skip length")
    remaining = length
    while remaining:
        block = handle.read(min(remaining, 65_536))
        if not block:
            raise GGUFMetadataError("Truncated GGUF metadata")
        remaining -= len(block)


def _read_value(handle: BinaryIO, value_type: int, *, capture: bool) -> Any:
    if value_type in _SCALAR_FORMATS:
        value_format = _SCALAR_FORMATS[value_type]
        if capture:
            return _unpack(handle, value_format)
        _skip_exact(handle, struct.calcsize("<" + value_format))
        return None
    if value_type == 8:  # string
        value = _read_string_bytes(handle)
        return _decode_string(value, field="value") if capture else None
    if value_type == 9:  # array
        element_type = _unpack(handle, "I")
        length = _unpack(handle, "Q")
        if length > _MAX_METADATA_ARRAY_ITEMS:
            raise GGUFMetadataError(f"GGUF metadata array is too large: {length} items")
        if element_type == 9:
            raise GGUFMetadataError("Nested GGUF metadata arrays are unsupported")
        if not capture and element_type in _SCALAR_FORMATS:
            _skip_exact(handle, length * struct.calcsize("<" + _SCALAR_FORMATS[element_type]))
            return None
        values = [_read_value(handle, element_type, capture=capture) for _ in range(length)]
        return values if capture else None
    raise GGUFMetadataError(f"Unsupported GGUF metadata value type: {value_type}")


def _array_digest(values: tuple[Any, ...]) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = value.encode("utf-8") if isinstance(value, str) else repr(value).encode("ascii")
        digest.update(len(encoded).to_bytes(8, "little"))
        digest.update(encoded)
    return digest.hexdigest()


def _special_token_digest(tokens: tuple[str, ...], token_types: tuple[int, ...]) -> str:
    items = [
        {"id": token_id, "token": token, "type": token_type}
        for token_id, (token, token_type) in enumerate(zip(tokens, token_types, strict=True))
        if token_type in _SPECIAL_TOKEN_TYPES
    ]
    encoded = json.dumps(
        items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tokenizer_metadata_digest(values: dict[str, Any]) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_gguf_tokenizer_metadata(model_blob_path: Path) -> GGUFTokenizerMetadata:
    """Read only tokenizer metadata from a GGUF header, never tensor payloads."""
    selected: dict[str, Any] = {}
    try:
        with model_blob_path.open("rb") as handle:
            if _read_exact(handle, 4) != b"GGUF":
                raise GGUFMetadataError("Model blob is not a GGUF file")
            version = _unpack(handle, "I")
            if version != 3:
                raise GGUFMetadataError(f"Unsupported GGUF version: {version}")
            _unpack(handle, "Q")  # tensor count; tensors are never read
            metadata_count = _unpack(handle, "Q")
            if metadata_count > 100_000:
                raise GGUFMetadataError(f"Implausible GGUF metadata entry count: {metadata_count}")
            for _ in range(metadata_count):
                key = _decode_string(_read_string_bytes(handle), field="metadata key")
                value_type = _unpack(handle, "I")
                value = _read_value(
                    handle,
                    value_type,
                    capture=key in _GGUF_SELECTED_KEYS,
                )
                if key in _GGUF_SELECTED_KEYS:
                    if key in selected:
                        raise GGUFMetadataError(f"Duplicate GGUF metadata key: {key}")
                    selected[key] = value
    except OSError as error:
        raise GGUFMetadataError(f"Cannot read GGUF model blob: {model_blob_path}") from error

    missing = sorted(_GGUF_SELECTED_KEYS - selected.keys())
    if missing:
        raise GGUFMetadataError("GGUF tokenizer metadata is incomplete: " + ", ".join(missing))
    tokens = tuple(selected["tokenizer.ggml.tokens"])
    merges = tuple(selected["tokenizer.ggml.merges"])
    token_types = tuple(selected["tokenizer.ggml.token_type"])
    if not all(isinstance(value, str) for value in tokens):
        raise GGUFMetadataError("GGUF tokenizer token array must contain strings")
    if not all(isinstance(value, str) for value in merges):
        raise GGUFMetadataError("GGUF tokenizer merge array must contain strings")
    if not all(isinstance(value, int) for value in token_types):
        raise GGUFMetadataError("GGUF tokenizer token-type array must contain integers")
    if len(tokens) != len(token_types):
        raise GGUFMetadataError("GGUF tokenizer token and token-type array lengths do not match")

    token_digest = _array_digest(tokens)
    merge_digest = _array_digest(merges)
    type_digest = _array_digest(token_types)
    special_digest = _special_token_digest(tokens, token_types)
    chat_template_digest = hashlib.sha256(
        selected["tokenizer.chat_template"].encode("utf-8")
    ).hexdigest()
    digest_values = {
        "gguf_version": version,
        "architecture": selected["general.architecture"],
        "model_name": selected["general.name"],
        "tokenizer_model": selected["tokenizer.ggml.model"],
        "tokenizer_pre": selected["tokenizer.ggml.pre"],
        "token_count": len(tokens),
        "merge_count": len(merges),
        "token_array_sha256": token_digest,
        "merge_array_sha256": merge_digest,
        "token_type_array_sha256": type_digest,
        "special_token_metadata_sha256": special_digest,
        "add_bos_token": selected["tokenizer.ggml.add_bos_token"],
        "bos_token_id": selected["tokenizer.ggml.bos_token_id"],
        "eos_token_id": selected["tokenizer.ggml.eos_token_id"],
        "padding_token_id": selected["tokenizer.ggml.padding_token_id"],
        "gguf_chat_template_sha256": chat_template_digest,
    }
    return GGUFTokenizerMetadata(
        gguf_version=version,
        architecture=selected["general.architecture"],
        model_name=selected["general.name"],
        tokenizer_model=selected["tokenizer.ggml.model"],
        tokenizer_pre=selected["tokenizer.ggml.pre"],
        tokens=tokens,
        merges=merges,
        token_types=token_types,
        add_bos_token=selected["tokenizer.ggml.add_bos_token"],
        bos_token_id=selected["tokenizer.ggml.bos_token_id"],
        eos_token_id=selected["tokenizer.ggml.eos_token_id"],
        padding_token_id=selected["tokenizer.ggml.padding_token_id"],
        gguf_chat_template_sha256=chat_template_digest,
        token_array_sha256=token_digest,
        merge_array_sha256=merge_digest,
        token_type_array_sha256=type_digest,
        special_token_metadata_sha256=special_digest,
        tokenizer_metadata_sha256=_tokenizer_metadata_digest(digest_values),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(4 * 1_048_576), b""):
                digest.update(block)
    except OSError as error:
        raise ExactTokenizerContractError(f"Cannot read pinned file: {path}") from error
    return digest.hexdigest()


def resolve_ollama_models_root(
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve Ollama's model root without a machine-specific absolute path."""
    values = os.environ if environ is None else environ
    configured = values.get("OLLAMA_MODELS")
    if configured:
        return Path(configured).expanduser().resolve()
    return ((home or Path.home()) / ".ollama" / "models").resolve()


def _manifest_path(models_root: Path, model_name: str) -> Path:
    if model_name != SUPPORTED_MODEL_NAME:
        raise ExactTokenizerContractError(
            f"Exact estimator supports only {SUPPORTED_MODEL_NAME}; got {model_name}"
        )
    name, tag = model_name.split(":", 1)
    return models_root / "manifests" / "registry.ollama.ai" / "library" / name / tag


def _blob_path(models_root: Path, digest: str) -> Path:
    algorithm, separator, value = digest.partition(":")
    if separator != ":" or algorithm != "sha256" or len(value) != 64:
        raise ExactTokenizerContractError(f"Unsupported Ollama blob digest: {digest}")
    try:
        int(value, 16)
    except ValueError as error:
        raise ExactTokenizerContractError(f"Malformed Ollama blob digest: {digest}") from error
    return models_root / "blobs" / f"sha256-{value}"


def _load_manifest(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        size = path.stat().st_size
        if size > _MAX_MANIFEST_BYTES:
            raise ExactTokenizerContractError(
                f"Ollama model manifest exceeds {_MAX_MANIFEST_BYTES} bytes"
            )
        raw = path.read_bytes()
    except OSError as error:
        raise ExactTokenizerContractError(f"Missing Ollama model manifest: {path}") from error
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExactTokenizerContractError("Ollama model manifest is invalid JSON") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("layers"), list):
        raise ExactTokenizerContractError("Ollama model manifest has no layers array")
    return raw, manifest


def _one_layer(manifest: dict[str, Any], media_type: str) -> dict[str, Any]:
    matches = [
        layer
        for layer in manifest["layers"]
        if isinstance(layer, dict) and layer.get("mediaType") == media_type
    ]
    if len(matches) != 1:
        raise ExactTokenizerContractError(
            f"Expected one Ollama {media_type} layer; found {len(matches)}"
        )
    return matches[0]


def _require_equal(name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise ExactTokenizerContractError(
            f"Pinned exact-tokenizer contract drifted: {name} expected {expected!r}, got {actual!r}"
        )


@lru_cache(maxsize=8)
def _resolve_exact_contract_cached(
    models_root_value: str,
    model_name: str,
    pins: ExactTokenizerPins,
) -> ExactTokenizerContract:
    models_root = Path(models_root_value)
    manifest_path = _manifest_path(models_root, model_name)
    manifest_raw, manifest = _load_manifest(manifest_path)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    _require_equal("requested model", model_name, pins.model_name)
    _require_equal("model manifest SHA-256", manifest_sha, pins.model_manifest_sha256)

    model_layer = _one_layer(manifest, _MODEL_MEDIA_TYPE)
    template_layer = _one_layer(manifest, _TEMPLATE_MEDIA_TYPE)
    model_digest = model_layer.get("digest")
    _require_equal("model blob digest", model_digest, pins.model_blob_digest)
    _require_equal("model blob size in manifest", model_layer.get("size"), pins.model_blob_size)
    template_digest = template_layer.get("digest")
    _require_equal(
        "Ollama template digest",
        template_digest,
        f"sha256:{pins.ollama_template_sha256}",
    )

    model_blob_path = _blob_path(models_root, model_digest)
    template_blob_path = _blob_path(models_root, template_digest)
    if not model_blob_path.is_file():
        raise ExactTokenizerContractError(f"Missing Ollama model blob: {model_blob_path}")
    if not template_blob_path.is_file():
        raise ExactTokenizerContractError(f"Missing Ollama template blob: {template_blob_path}")
    _require_equal("model blob file size", model_blob_path.stat().st_size, pins.model_blob_size)
    _require_equal(
        "template blob file size",
        template_blob_path.stat().st_size,
        template_layer.get("size"),
    )
    model_blob_sha = _sha256_file(model_blob_path)
    _require_equal("model blob content SHA-256", model_blob_sha, model_digest.split(":", 1)[1])
    template_blob_sha = _sha256_file(template_blob_path)
    _require_equal(
        "Ollama template content SHA-256", template_blob_sha, pins.ollama_template_sha256
    )

    metadata = read_gguf_tokenizer_metadata(model_blob_path)
    _require_equal("GGUF architecture", metadata.architecture, "qwen3")
    _require_equal("tokenizer model", metadata.tokenizer_model, pins.tokenizer_model)
    _require_equal("tokenizer pre-tokenizer", metadata.tokenizer_pre, pins.tokenizer_pre)
    _require_equal("token count", len(metadata.tokens), pins.token_count)
    _require_equal("merge count", len(metadata.merges), pins.merge_count)
    _require_equal("token array SHA-256", metadata.token_array_sha256, pins.token_array_sha256)
    _require_equal("merge array SHA-256", metadata.merge_array_sha256, pins.merge_array_sha256)
    _require_equal(
        "token-type array SHA-256",
        metadata.token_type_array_sha256,
        pins.token_type_array_sha256,
    )
    _require_equal(
        "special-token metadata SHA-256",
        metadata.special_token_metadata_sha256,
        pins.special_token_metadata_sha256,
    )
    _require_equal(
        "tokenizer metadata SHA-256",
        metadata.tokenizer_metadata_sha256,
        pins.tokenizer_metadata_sha256,
    )
    _require_equal(
        "GGUF chat-template SHA-256",
        metadata.gguf_chat_template_sha256,
        pins.gguf_chat_template_sha256,
    )
    _require_equal("add_bos_token", metadata.add_bos_token, pins.add_bos_token)
    _require_equal("BOS token ID", metadata.bos_token_id, pins.bos_token_id)
    _require_equal("EOS token ID", metadata.eos_token_id, pins.eos_token_id)
    _require_equal("padding token ID", metadata.padding_token_id, pins.padding_token_id)
    _require_equal("request API mode", pins.request_api_mode, SUPPORTED_REQUEST_API_MODE)
    if not pins.framing_contract_version or pins.framing_token_count < 0:
        raise ExactTokenizerContractError("Pinned framing verification metadata is absent")

    return ExactTokenizerContract(
        estimator_version=EXACT_PROMPT_ESTIMATOR_VERSION,
        requested_model_name=model_name,
        pin_set_id=pins.pin_set_id,
        model_manifest_sha256=manifest_sha,
        model_blob_digest=model_digest,
        model_blob_sha256=model_blob_sha,
        model_blob_size=model_blob_path.stat().st_size,
        ollama_template_sha256=template_blob_sha,
        tokenizer_model=metadata.tokenizer_model,
        tokenizer_pre=metadata.tokenizer_pre,
        token_count=len(metadata.tokens),
        merge_count=len(metadata.merges),
        tokenizer_metadata_sha256=metadata.tokenizer_metadata_sha256,
        tokenizer_token_array_sha256=metadata.token_array_sha256,
        tokenizer_merge_array_sha256=metadata.merge_array_sha256,
        tokenizer_token_type_array_sha256=metadata.token_type_array_sha256,
        special_token_metadata_sha256=metadata.special_token_metadata_sha256,
        gguf_chat_template_sha256=metadata.gguf_chat_template_sha256,
        add_bos_token=metadata.add_bos_token,
        bos_token_id=metadata.bos_token_id,
        eos_token_id=metadata.eos_token_id,
        padding_token_id=metadata.padding_token_id,
        request_api_mode=pins.request_api_mode,
        framing_contract_version=pins.framing_contract_version,
        framing_token_count=pins.framing_token_count,
        ollama_version=pins.ollama_version,
        model_blob_path=model_blob_path,
    )


def resolve_exact_tokenizer_contract(
    model_name: str,
    *,
    models_root: Path | None = None,
    pins: ExactTokenizerPins = PINNED_QWEN3_14B_PINS,
) -> ExactTokenizerContract:
    """Resolve and fully verify the pinned local tokenizer contract."""
    root = (models_root or resolve_ollama_models_root()).resolve()
    return _resolve_exact_contract_cached(str(root), model_name, pins)


def _bytes_to_unicode() -> dict[int, str]:
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


def _is_letter(value: str) -> bool:
    return unicodedata.category(value).startswith("L")


def _is_number(value: str) -> bool:
    return unicodedata.category(value).startswith("N")


def qwen2_pretokens(text: str) -> tuple[str, ...]:
    """Match Qwen2's Unicode-aware, byte-level pre-tokenization regex."""
    output: list[str] = []
    index = 0
    contractions = ("'re", "'ve", "'ll", "'s", "'t", "'m", "'d")
    while index < len(text):
        lowered = text[index : index + 3].lower()
        contraction = next(
            (value for value in contractions if lowered.startswith(value)),
            None,
        )
        if contraction is not None:
            output.append(text[index : index + len(contraction)])
            index += len(contraction)
            continue

        value = text[index]
        if _is_letter(value):
            end = index + 1
            while end < len(text) and _is_letter(text[end]):
                end += 1
            output.append(text[index:end])
            index = end
            continue
        if value not in "\r\n" and not _is_letter(value) and not _is_number(value):
            if index + 1 < len(text) and _is_letter(text[index + 1]):
                end = index + 2
                while end < len(text) and _is_letter(text[end]):
                    end += 1
                output.append(text[index:end])
                index = end
                continue

        if _is_number(value):
            end = index + 1
            while end < min(len(text), index + 3) and _is_number(text[end]):
                end += 1
            output.append(text[index:end])
            index = end
            continue

        punctuation_start = index
        if value == " " and index + 1 < len(text):
            candidate = text[index + 1]
            if not candidate.isspace() and not _is_letter(candidate) and not _is_number(candidate):
                punctuation_start = index + 1
        candidate = text[punctuation_start]
        if not candidate.isspace() and not _is_letter(candidate) and not _is_number(candidate):
            end = punctuation_start + 1
            while end < len(text):
                candidate = text[end]
                if candidate.isspace() or _is_letter(candidate) or _is_number(candidate):
                    break
                end += 1
            while end < len(text) and text[end] in "\r\n":
                end += 1
            output.append(text[index:end])
            index = end
            continue

        if value.isspace():
            run_end = index + 1
            while run_end < len(text) and text[run_end].isspace():
                run_end += 1
            last_newline = max(
                text.rfind("\n", index, run_end),
                text.rfind("\r", index, run_end),
            )
            if last_newline >= index:
                end = last_newline + 1
            elif run_end == len(text):
                end = run_end
            elif run_end - index > 1:
                end = run_end - 1
            else:
                end = run_end
            output.append(text[index:end])
            index = end
            continue
        raise GGUFMetadataError(
            f"Qwen2 pre-tokenizer could not classify Unicode category {unicodedata.category(value)}"
        )
    return tuple(output)


class Qwen3GGUFTokenizer:
    """Immutable GPT-2 byte BPE using the Qwen2 pre-tokenization contract."""

    def __init__(self, metadata: GGUFTokenizerMetadata):
        if metadata.tokenizer_model != "gpt2":
            raise ExactTokenizerContractError(
                f"Unsupported tokenizer model: {metadata.tokenizer_model}"
            )
        if metadata.tokenizer_pre != "qwen2":
            raise ExactTokenizerContractError(
                f"Unsupported tokenizer pre-tokenizer: {metadata.tokenizer_pre}"
            )
        if metadata.add_bos_token:
            raise ExactTokenizerContractError(
                "Pinned exact estimator does not support implicit BOS insertion"
            )
        self._tokens = metadata.tokens
        self._vocabulary = {token: index for index, token in enumerate(metadata.tokens)}
        if len(self._vocabulary) != len(metadata.tokens):
            raise ExactTokenizerContractError("Tokenizer vocabulary contains duplicate tokens")
        ranks: dict[tuple[str, str], int] = {}
        for rank, value in enumerate(metadata.merges):
            try:
                left, right = value.split(" ", 1)
            except ValueError as error:
                raise ExactTokenizerContractError(
                    f"Malformed tokenizer merge at rank {rank}"
                ) from error
            pair = (left, right)
            if pair in ranks:
                raise ExactTokenizerContractError(f"Duplicate tokenizer merge pair at rank {rank}")
            ranks[pair] = rank
        self._ranks = ranks
        self._specials = tuple(
            sorted(
                (
                    token
                    for token, token_type in zip(
                        metadata.tokens,
                        metadata.token_types,
                        strict=True,
                    )
                    if token_type in _SPECIAL_TOKEN_TYPES
                ),
                key=lambda token: (-len(token), token),
            )
        )
        self._byte_encoder = _bytes_to_unicode()
        self._bpe_cache: dict[str, tuple[str, ...]] = {}
        self._count_cache: dict[str, int] = {}

    def _bpe(self, token: str) -> tuple[str, ...]:
        cached = self._bpe_cache.get(token)
        if cached is not None:
            return cached
        word = tuple(token)
        while len(word) > 1:
            ranked = [
                (self._ranks[pair], pair) for pair in zip(word, word[1:]) if pair in self._ranks
            ]
            if not ranked:
                break
            _, best = min(ranked)
            merged: list[str] = []
            index = 0
            while index < len(word):
                if index + 1 < len(word) and (word[index], word[index + 1]) == best:
                    merged.append(word[index] + word[index + 1])
                    index += 2
                else:
                    merged.append(word[index])
                    index += 1
            word = tuple(merged)
        if len(self._bpe_cache) < 200_000:
            self._bpe_cache[token] = word
        return word

    def _encode_regular(self, text: str) -> list[int]:
        output: list[int] = []
        for pretoken in qwen2_pretokens(text):
            encoded = "".join(self._byte_encoder[value] for value in pretoken.encode("utf-8"))
            for piece in self._bpe(encoded):
                token_id = self._vocabulary.get(piece)
                if token_id is None:
                    piece_hash = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                    raise ExactTokenizerContractError(
                        f"BPE piece absent from pinned vocabulary: {piece_hash}"
                    )
                output.append(token_id)
        return output

    def encode(self, text: str) -> tuple[int, ...]:
        output: list[int] = []
        cursor = 0
        while cursor < len(text):
            positions = [
                (position, special)
                for special in self._specials
                if (position := text.find(special, cursor)) >= 0
            ]
            if not positions:
                output.extend(self._encode_regular(text[cursor:]))
                break
            position, special = min(positions, key=lambda item: (item[0], -len(item[1])))
            output.extend(self._encode_regular(text[cursor:position]))
            output.append(self._vocabulary[special])
            cursor = position + len(special)
        return tuple(output)

    def count(self, text: str) -> int:
        cached = self._count_cache.get(text)
        if cached is not None:
            return cached
        count = len(self.encode(text))
        if len(self._count_cache) < 100_000:
            self._count_cache[text] = count
        return count


@lru_cache(maxsize=8)
def _load_tokenizer_cached(
    model_blob_path_value: str,
    cache_key: str,
    tokenizer_metadata_sha256: str,
    token_array_sha256: str,
    merge_array_sha256: str,
    token_type_array_sha256: str,
    special_token_metadata_sha256: str,
) -> Qwen3GGUFTokenizer:
    # cache_key is intentionally part of the key: changed tokenizer/model
    # digests can never reuse an object parsed under an older contract.
    if not cache_key:
        raise ExactTokenizerContractError("Tokenizer cache key is missing")
    metadata = read_gguf_tokenizer_metadata(Path(model_blob_path_value))
    expected = {
        "tokenizer metadata SHA-256": tokenizer_metadata_sha256,
        "token array SHA-256": token_array_sha256,
        "merge array SHA-256": merge_array_sha256,
        "token-type array SHA-256": token_type_array_sha256,
        "special-token metadata SHA-256": special_token_metadata_sha256,
    }
    actual = {
        "tokenizer metadata SHA-256": metadata.tokenizer_metadata_sha256,
        "token array SHA-256": metadata.token_array_sha256,
        "merge array SHA-256": metadata.merge_array_sha256,
        "token-type array SHA-256": metadata.token_type_array_sha256,
        "special-token metadata SHA-256": metadata.special_token_metadata_sha256,
    }
    for name, expected_value in expected.items():
        _require_equal(name, actual[name], expected_value)
    return Qwen3GGUFTokenizer(metadata)


def count_visible_prompt_tokens(prompt: str, contract: ExactTokenizerContract) -> int:
    if contract.estimator_version != EXACT_PROMPT_ESTIMATOR_VERSION:
        raise ExactTokenizerContractError(
            f"Unsupported exact estimator version: {contract.estimator_version}"
        )
    tokenizer = _load_tokenizer_cached(
        str(contract.model_blob_path),
        contract.cache_key,
        contract.tokenizer_metadata_sha256,
        contract.tokenizer_token_array_sha256,
        contract.tokenizer_merge_array_sha256,
        contract.tokenizer_token_type_array_sha256,
        contract.special_token_metadata_sha256,
    )
    return tokenizer.count(prompt)


def estimate_server_prompt_tokens(
    prompt: str,
    contract: ExactTokenizerContract,
    *,
    request_api_mode: str = SUPPORTED_REQUEST_API_MODE,
) -> int:
    """Count visible prompt tokens plus verified server-side framing tokens.

    Ollama's separately transported JSON Schema ``format`` field constrains the
    grammar.  It was not observed as textual input to ``prompt_eval_count`` and
    is therefore not tokenized here.
    """
    if request_api_mode != contract.request_api_mode:
        raise ExactTokenizerContractError(
            f"Unsupported request API mode {request_api_mode!r}; "
            f"contract requires {contract.request_api_mode!r}"
        )
    if (
        not contract.framing_contract_version
        or contract.framing_token_count < 0
        or contract.ollama_version != PINNED_OLLAMA_VERSION
    ):
        raise ExactTokenizerContractError("Verified server framing metadata is absent or stale")
    return count_visible_prompt_tokens(prompt, contract) + contract.framing_token_count
