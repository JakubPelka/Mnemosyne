from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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
    context_id: Mapped[str | None] = mapped_column(String(255), index=True)
    timestamp_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    timestamp_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    location_id: Mapped[str | None] = mapped_column(String(128))
    privacy_level: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )
    analysis_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )
    raw_payload_reference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventSegment(Base):
    __tablename__ = "event_segments"
    __table_args__ = (
        UniqueConstraint("event_id", "segment_index"),
        CheckConstraint(
            "segment_type IN ('prose', 'code', 'inline_code', 'shell_command', 'log', "
            "'quote', 'table', 'tool_artifact', 'link', 'unknown')",
            name="event_segment_type",
        ),
    )

    segment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    search_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    topic_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active", index=True
    )
    origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default="automatic", server_default="automatic"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    conversation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CandidateTerm(Base):
    __tablename__ = "candidate_terms"
    __table_args__ = (
        UniqueConstraint("normalized_term"),
        CheckConstraint("ngram_size >= 1 AND ngram_size <= 3", name="candidate_ngram_size"),
        CheckConstraint(
            "quality_status IN ('accepted', 'rejected', 'legacy')",
            name="candidate_quality_status",
        ),
    )

    term_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_term: Mapped[str] = mapped_column(Text, nullable=False)
    ngram_size: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), index=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    document_frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tfidf_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(64), index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )


class TopicTerm(Base):
    __tablename__ = "topic_terms"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('primary', 'alias', 'manual')", name="topic_term_relation_type"
        ),
    )

    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topics.topic_id", ondelete="CASCADE"), primary_key=True
    )
    term_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_terms.term_id", ondelete="CASCADE"), primary_key=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="alias")


class EventCandidateTerm(Base):
    __tablename__ = "event_candidate_terms"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    term_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_terms.term_id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class EventTopic(Base):
    __tablename__ = "event_topics"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topics.topic_id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class TopicRelation(Base):
    __tablename__ = "topic_relations"
    __table_args__ = (UniqueConstraint("source_topic_id", "target_topic_id"),)

    relation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_topic_id: Mapped[str] = mapped_column(
        ForeignKey("topics.topic_id", ondelete="CASCADE"), nullable=False
    )
    target_topic_id: Mapped[str] = mapped_column(
        ForeignKey("topics.topic_id", ondelete="CASCADE"), nullable=False
    )
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


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
