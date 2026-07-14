from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.importers.chatgpt import ChatGPTConversation, ChatGPTExportAdapter
from backend.app.models import (
    ChatGPTConversationModel,
    ChatGPTMessageModel,
    Event,
    ImportRun,
    Source,
)

_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class ImportResult:
    import_run_id: str
    conversations: int
    events: int
    warnings: int


def import_chatgpt_export(
    session: Session,
    input_path: Path,
    *,
    source_id: str = "chatgpt-default",
    source_name: str = "ChatGPT",
) -> ImportResult:
    """Import or update an export in one transaction without duplicating source records."""

    started_at = datetime.now(UTC)
    path_hash = hashlib.sha256(str(Path(input_path).resolve()).encode()).hexdigest()
    run_id = str(uuid.uuid4())
    adapter = ChatGPTExportAdapter()

    _upsert(
        session,
        Source,
        [
            {
                "source_id": source_id,
                "source_type": "chatgpt",
                "name": source_name,
                "imported_at": started_at,
                "source_version": None,
                "original_path_hash": path_hash,
                "metadata": {"format": "openai_chatgpt_export"},
            }
        ],
        ("source_id",),
        ("name", "imported_at", "source_version", "original_path_hash", "metadata"),
    )
    session.add(
        ImportRun(
            import_run_id=run_id,
            source_id=source_id,
            started_at=started_at,
            status="running",
            input_path_hash=path_hash,
        )
    )
    session.flush()
    session.execute(update(Event).where(Event.source_id == source_id).values(is_active=False))

    conversation_count = 0
    event_count = 0
    batch: list[ChatGPTConversation] = []
    for conversation in adapter.parse(Path(input_path)):
        batch.append(conversation)
        if len(batch) >= _BATCH_SIZE:
            imported_conversations, imported_events = _import_batch(
                session, source_id, batch, started_at
            )
            conversation_count += imported_conversations
            event_count += imported_events
            batch.clear()
    if batch:
        imported_conversations, imported_events = _import_batch(
            session, source_id, batch, started_at
        )
        conversation_count += imported_conversations
        event_count += imported_events

    run = session.get(ImportRun, run_id)
    if run is None:  # pragma: no cover - protected by the transaction above
        raise RuntimeError("import_run_not_found")
    run.status = "completed"
    run.finished_at = datetime.now(UTC)
    run.imported_conversations = conversation_count
    run.imported_events = event_count
    run.warning_count = len(adapter.warnings)
    session.commit()
    return ImportResult(run_id, conversation_count, event_count, len(adapter.warnings))


def _import_batch(
    session: Session,
    source_id: str,
    conversations: Iterable[ChatGPTConversation],
    imported_at: datetime,
) -> tuple[int, int]:
    event_rows: list[dict[str, Any]] = []
    conversation_rows: list[dict[str, Any]] = []
    message_rows: list[dict[str, Any]] = []
    conversation_count = 0

    for conversation in conversations:
        conversation_count += 1
        conversation_event_id = _stable_id("conversation", conversation.conversation_id)
        event_rows.append(
            {
                "event_id": conversation_event_id,
                "source_id": source_id,
                "source_record_id": f"conversation:{conversation.conversation_id}",
                "event_type": "chatgpt_conversation",
                "context_id": conversation.conversation_id,
                "timestamp_start": conversation.created_at,
                "timestamp_end": conversation.updated_at,
                "title": conversation.title,
                "text": None,
                "privacy_level": "private",
                "is_active": True,
                "analysis_enabled": False,
                "raw_payload_reference": f"conversation:{conversation.conversation_id}",
                "created_at": imported_at,
                "updated_at": imported_at,
            }
        )
        conversation_rows.append(
            {
                "conversation_id": conversation.conversation_id,
                "source_id": source_id,
                "event_id": conversation_event_id,
                "title": conversation.title,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "message_count": conversation.message_count,
            }
        )
        for message in conversation.messages:
            scoped_message_id = f"{conversation.conversation_id}:{message.message_id}"
            message_event_id = _stable_id("message", scoped_message_id)
            event_rows.append(
                {
                    "event_id": message_event_id,
                    "source_id": source_id,
                    "source_record_id": f"message:{scoped_message_id}",
                    "event_type": "chatgpt_message",
                    "context_id": conversation.conversation_id,
                    "timestamp_start": message.created_at,
                    "timestamp_end": None,
                    "title": conversation.title,
                    "text": message.text,
                    "privacy_level": "private",
                    "is_active": True,
                    "analysis_enabled": message.content_type in {"text", "multimodal_text"},
                    "raw_payload_reference": (
                        f"conversation:{conversation.conversation_id}/message:{message.message_id}"
                    ),
                    "created_at": imported_at,
                    "updated_at": imported_at,
                }
            )
            message_rows.append(
                {
                    "message_key": _stable_id("message-key", scoped_message_id),
                    "message_id": message.message_id,
                    "conversation_id": conversation.conversation_id,
                    "event_id": message_event_id,
                    "parent_message_key": (
                        _stable_id(
                            "message-key",
                            f"{conversation.conversation_id}:{message.parent_message_id}",
                        )
                        if message.parent_message_id
                        else None
                    ),
                    "parent_message_id": message.parent_message_id,
                    "role": message.role,
                    "created_at": message.created_at,
                    "sequence_number": message.sequence_number,
                    "content_type": message.content_type,
                    "is_on_current_branch": message.is_on_current_branch,
                }
            )

    _upsert(
        session,
        Event,
        event_rows,
        ("source_id", "source_record_id"),
        (
            "event_type",
            "context_id",
            "timestamp_start",
            "timestamp_end",
            "title",
            "text",
            "privacy_level",
            "is_active",
            "analysis_enabled",
            "raw_payload_reference",
            "updated_at",
        ),
    )
    _upsert(
        session,
        ChatGPTConversationModel,
        conversation_rows,
        ("conversation_id",),
        ("event_id", "title", "created_at", "updated_at", "message_count"),
    )
    _upsert(
        session,
        ChatGPTMessageModel,
        message_rows,
        ("message_key",),
        (
            "message_id",
            "conversation_id",
            "event_id",
            "parent_message_key",
            "parent_message_id",
            "role",
            "created_at",
            "sequence_number",
            "content_type",
            "is_on_current_branch",
        ),
    )
    session.flush()
    return conversation_count, len(event_rows)


def _upsert(
    session: Session,
    model: type[Any],
    rows: list[dict[str, Any]],
    conflict_columns: tuple[str, ...],
    update_columns: tuple[str, ...],
) -> None:
    if not rows:
        return
    table = model.__table__
    statement = insert(table)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c[name] for name in conflict_columns],
        set_={name: getattr(statement.excluded, name) for name in update_columns},
    )
    session.execute(statement, rows)


def _stable_id(record_type: str, source_record_id: str) -> str:
    digest = hashlib.sha256(f"chatgpt:{record_type}:{source_record_id}".encode()).hexdigest()
    return f"chatgpt-{record_type}-{digest}"
