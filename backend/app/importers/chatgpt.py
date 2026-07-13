from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence
from zipfile import BadZipFile, ZipFile

from .base import InspectionReport, NormalizedEvent, SourceMetadata

_CONVERSATION_FILE_PREFIX = "conversations-"
_CONVERSATION_FILE_NAME = "conversations.json"
_MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024


class ChatGPTExportError(ValueError):
    """A content-safe error raised for an invalid or unsupported export."""


@dataclass(frozen=True, slots=True)
class ImportWarning:
    code: str
    record_number: int | None = None


@dataclass(frozen=True, slots=True)
class ChatGPTMessage:
    message_id: str
    conversation_id: str
    parent_message_id: str | None
    role: str
    created_at: datetime | None
    text: str | None
    sequence_number: int
    content_type: str
    is_on_current_branch: bool


@dataclass(frozen=True, slots=True)
class ChatGPTConversation:
    conversation_id: str
    title: str | None
    created_at: datetime | None
    updated_at: datetime | None
    messages: tuple[ChatGPTMessage, ...]
    source: str = "chatgpt"

    @property
    def message_count(self) -> int:
        return len(self.messages)


@dataclass(slots=True)
class ChatGPTExportAdapter:
    """Read-only adapter for directory and ZIP variants of ChatGPT exports."""

    warnings: list[ImportWarning] = field(default_factory=list, init=False)

    def inspect(self, input_path: Path) -> InspectionReport:
        try:
            with _export_reader(input_path) as reader:
                candidates = _conversation_file_names(reader.names)
                warnings: list[str] = []
                if not candidates:
                    warnings.append("conversation_files_not_found")
                return InspectionReport(
                    detected_format="openai_chatgpt_export",
                    input_kind=reader.kind,
                    candidate_files=tuple(PurePosixPath(name).name for name in candidates),
                    file_count=len(reader.names),
                    warnings=tuple(warnings),
                )
        except ChatGPTExportError:
            raise
        except (BadZipFile, OSError) as exc:
            raise ChatGPTExportError("export_cannot_be_inspected") from exc

    def validate(self, input_path: Path) -> None:
        try:
            with _export_reader(input_path) as reader:
                if reader.unsafe_names:
                    raise ChatGPTExportError("archive_contains_unsafe_paths")
                if reader.uncompressed_size > _MAX_UNCOMPRESSED_BYTES:
                    raise ChatGPTExportError("archive_uncompressed_size_limit_exceeded")
                candidates = _conversation_file_names(reader.names)
                if not candidates:
                    raise ChatGPTExportError("conversation_files_not_found")
                for name in candidates:
                    value = reader.read_json(name)
                    if not isinstance(value, list):
                        raise ChatGPTExportError("conversation_file_must_contain_a_list")
        except ChatGPTExportError:
            raise
        except (BadZipFile, json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise ChatGPTExportError("export_validation_failed") from exc

    def parse(self, input_path: Path) -> Iterator[ChatGPTConversation]:
        self.warnings.clear()
        self.validate(input_path)
        try:
            with _export_reader(input_path) as reader:
                record_number = 0
                for name in _conversation_file_names(reader.names):
                    conversations = reader.read_json(name)
                    for raw in conversations:
                        record_number += 1
                        if not isinstance(raw, dict):
                            self.warnings.append(
                                ImportWarning("conversation_record_not_an_object", record_number)
                            )
                            continue
                        conversation = self._parse_conversation(raw, record_number)
                        if conversation is not None:
                            yield conversation
        except ChatGPTExportError:
            raise
        except (BadZipFile, json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise ChatGPTExportError("export_parse_failed") from exc

    def normalize(self, raw_record: Any) -> Iterator[NormalizedEvent]:
        if not isinstance(raw_record, ChatGPTConversation):
            raise TypeError("raw_record_must_be_a_chatgpt_conversation")
        for message in raw_record.messages:
            yield NormalizedEvent(
                source_record_id=message.message_id,
                event_type="chatgpt_message",
                timestamp_start=message.created_at,
                timestamp_end=None,
                title=raw_record.title,
                text=message.text,
                privacy_level="private",
                metadata={
                    "conversation_id": raw_record.conversation_id,
                    "parent_message_id": message.parent_message_id,
                    "role": message.role,
                    "sequence_number": message.sequence_number,
                    "content_type": message.content_type,
                    "is_on_current_branch": message.is_on_current_branch,
                    "source_type": "chatgpt",
                },
            )

    def get_source_metadata(self) -> SourceMetadata:
        return SourceMetadata(
            source_type="chatgpt",
            source_version=None,
            metadata={"format": "openai_chatgpt_export"},
        )

    def _parse_conversation(
        self, raw: Mapping[str, Any], record_number: int
    ) -> ChatGPTConversation | None:
        mapping = raw.get("mapping")
        if not isinstance(mapping, dict):
            self.warnings.append(ImportWarning("conversation_mapping_missing", record_number))
            return None

        conversation_id = _source_id(raw)
        ordered_node_ids = _ordered_node_ids(mapping)
        current_branch = _current_branch(mapping, raw.get("current_node"))
        messages: list[ChatGPTMessage] = []

        for node_id in ordered_node_ids:
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                self.warnings.append(ImportWarning("message_node_not_an_object", record_number))
                continue
            message = node.get("message")
            if message is None:
                continue
            if not isinstance(message, dict):
                self.warnings.append(ImportWarning("message_not_an_object", record_number))
                continue

            author = message.get("author")
            content = message.get("content")
            message_id = str(message.get("id") or node.get("id") or node_id)
            parent_id = _parent_message_id(mapping, node.get("parent"))
            role = str(author.get("role") or "unknown") if isinstance(author, dict) else "unknown"
            content_type, text = _extract_content(content)
            messages.append(
                ChatGPTMessage(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    parent_message_id=parent_id,
                    role=role,
                    created_at=_timestamp(message.get("create_time")),
                    text=text,
                    sequence_number=len(messages),
                    content_type=content_type,
                    is_on_current_branch=node_id in current_branch,
                )
            )

        title = raw.get("title")
        return ChatGPTConversation(
            conversation_id=conversation_id,
            title=title if isinstance(title, str) else None,
            created_at=_timestamp(raw.get("create_time")),
            updated_at=_timestamp(raw.get("update_time")),
            messages=tuple(messages),
        )


@dataclass(frozen=True, slots=True)
class _ExportReader:
    kind: str
    names: tuple[str, ...]
    read_json: Any
    unsafe_names: tuple[str, ...] = ()
    uncompressed_size: int = 0


@contextmanager
def _export_reader(input_path: Path) -> Iterator[_ExportReader]:
    path = Path(input_path)
    if path.is_dir():
        files = tuple(sorted(file for file in path.rglob("*") if file.is_file()))
        relative_names = tuple(file.relative_to(path).as_posix() for file in files)

        def read_json(name: str) -> Any:
            return json.loads((path / PurePosixPath(name)).read_text(encoding="utf-8"))

        yield _ExportReader(
            kind="directory",
            names=relative_names,
            read_json=read_json,
            uncompressed_size=sum(file.stat().st_size for file in files),
        )
        return

    if not path.is_file():
        raise ChatGPTExportError("export_path_not_found")

    with ZipFile(path) as archive:
        infos = archive.infolist()
        names = tuple(info.filename for info in infos if not info.is_dir())
        unsafe = tuple(name for name in names if _unsafe_archive_name(name))

        def read_json(name: str) -> Any:
            with archive.open(name) as handle:
                return json.load(handle)

        yield _ExportReader(
            kind="zip",
            names=names,
            read_json=read_json,
            unsafe_names=unsafe,
            uncompressed_size=sum(info.file_size for info in infos),
        )


def _conversation_file_names(names: Sequence[str]) -> tuple[str, ...]:
    candidates = []
    for name in names:
        basename = PurePosixPath(name).name
        if basename == _CONVERSATION_FILE_NAME or (
            basename.startswith(_CONVERSATION_FILE_PREFIX) and basename.endswith(".json")
        ):
            candidates.append(name)
    return tuple(sorted(candidates))


def _unsafe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or "\\" in name


def _source_id(raw: Mapping[str, Any]) -> str:
    source_id = raw.get("id") or raw.get("conversation_id")
    if source_id:
        return str(source_id)
    structural_fallback = json.dumps(
        {
            "create_time": raw.get("create_time"),
            "update_time": raw.get("update_time"),
            "mapping_keys": sorted(str(key) for key in (raw.get("mapping") or {})),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(structural_fallback.encode()).hexdigest()
    return f"missing-id-{digest[:24]}"


def _ordered_node_ids(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    children: dict[str | None, list[str]] = defaultdict(list)
    for node_id, node in mapping.items():
        parent = node.get("parent") if isinstance(node, dict) else None
        if parent not in mapping:
            parent = None
        children[parent].append(str(node_id))

    def sort_key(node_id: str) -> tuple[bool, float, str]:
        node = mapping.get(node_id)
        message = node.get("message") if isinstance(node, dict) else None
        value = message.get("create_time") if isinstance(message, dict) else None
        numeric = float(value) if isinstance(value, (int, float)) else 0.0
        return value is None, numeric, node_id

    for node_ids in children.values():
        node_ids.sort(key=sort_key)

    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        ordered.append(node_id)
        for child_id in children.get(node_id, ()):
            visit(child_id)

    for root_id in children.get(None, ()):
        visit(root_id)
    for node_id in sorted((str(key) for key in mapping), key=sort_key):
        visit(node_id)
    return tuple(ordered)


def _current_branch(mapping: Mapping[str, Any], current_node: Any) -> frozenset[str]:
    branch: set[str] = set()
    node_id = str(current_node) if current_node is not None else None
    while node_id is not None and node_id not in branch:
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            break
        branch.add(node_id)
        parent = node.get("parent")
        node_id = str(parent) if parent is not None else None
    return frozenset(branch)


def _parent_message_id(mapping: Mapping[str, Any], parent_node_id: Any) -> str | None:
    if parent_node_id is None:
        return None
    parent = mapping.get(str(parent_node_id))
    if not isinstance(parent, dict):
        return None
    message = parent.get("message")
    if not isinstance(message, dict):
        return None
    return str(message.get("id") or parent.get("id") or parent_node_id)


def _extract_content(content: Any) -> tuple[str, str | None]:
    if not isinstance(content, dict):
        return "unknown", None
    content_type = str(content.get("content_type") or "unknown")
    if content_type == "thoughts":
        return content_type, None
    if content_type == "reasoning_recap":
        value = content.get("content")
        return content_type, value if isinstance(value, str) and value else None

    parts = content.get("parts")
    if not isinstance(parts, list):
        return content_type, None
    text_parts: list[str] = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
    text = "\n".join(part for part in text_parts if part)
    return content_type, text or None


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
