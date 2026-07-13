from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(128))
    original_path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source_id", "source_record_id"),
        CheckConstraint(
            "privacy_level IN ('private', 'sensitive', 'personal', 'public')",
            name="privacy_level",
        ),
        Index("ix_events_timestamp_range", "timestamp_start", "timestamp_end"),
        Index("ix_events_source_type_time", "source_id", "event_type", "timestamp_start"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False
    )
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    timestamp_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    location_id: Mapped[str | None] = mapped_column(String(128))
    privacy_level: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    raw_payload_reference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("entity_type", "normalized_name"),)

    entity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("name", "category"),)

    topic_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="keyword")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventTopic(Base):
    __tablename__ = "event_topics"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topics.topic_id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class EventEntity(Base):
    __tablename__ = "event_entities"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("entities.entity_id", ondelete="CASCADE"), primary_key=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), primary_key=True)


class EventRelation(Base):
    __tablename__ = "event_relations"
    __table_args__ = (UniqueConstraint("source_event_id", "target_event_id", "relation_type"),)

    relation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False
    )
    target_event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class ImportRun(Base):
    __tablename__ = "import_runs"

    import_run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.source_id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_conversations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128))
