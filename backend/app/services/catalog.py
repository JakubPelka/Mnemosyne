from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import distinct, func, or_, select, text
from sqlalchemy.orm import Session

from backend.app.models import (
    CandidateTerm,
    ChatGPTMessageModel,
    Event,
    EventCandidateTerm,
    EventTopic,
    Source,
    Topic,
    TopicRelation,
    TopicTerm,
)
from backend.app.services.topics import (
    MonthlyIntensity,
    term_monthly_intensity,
    topic_monthly_intensity,
)

_SNIPPET_LENGTH = 280
_SEARCH_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CatalogMeta:
    earliest_event_at: datetime | None
    latest_event_at: datetime | None
    source_types: tuple[str, ...]
    topic_categories: tuple[str, ...]
    event_count: int
    topic_count: int
    relation_count: int


@dataclass(frozen=True, slots=True)
class TopicSummary:
    topic_id: str
    name: str
    category: str
    message_count: int
    context_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    layer: str = "topics"


@dataclass(frozen=True, slots=True)
class TopicNeighbor:
    topic_id: str
    name: str
    category: str
    message_count: int
    context_count: int
    weight: float


@dataclass(frozen=True, slots=True)
class TopicDetail:
    summary: TopicSummary
    months: tuple[MonthlyIntensity, ...]
    neighbors: tuple[TopicNeighbor, ...]


@dataclass(frozen=True, slots=True)
class TopicTermSummary:
    term_id: str
    term: str
    ngram_size: int
    language: str | None
    message_count: int
    context_count: int
    document_frequency: int
    tfidf_score: float
    quality_score: float
    quality_status: str
    rejection_reason: str | None
    relation_type: str


@dataclass(frozen=True, slots=True)
class EventExcerpt:
    event_id: str
    occurred_at: datetime | None
    role: str | None
    conversation_title: str | None
    snippet: str
    source_record_id: str


@dataclass(frozen=True, slots=True)
class EventExcerptPage:
    items: tuple[EventExcerpt, ...]
    total: int
    limit: int
    offset: int


def get_catalog_meta(session: Session) -> CatalogMeta:
    active = Event.is_active.is_(True)
    earliest, latest, event_count = session.execute(
        select(
            func.min(Event.timestamp_start),
            func.max(Event.timestamp_start),
            func.count(Event.event_id),
        ).where(active)
    ).one()
    source_types = session.scalars(
        select(distinct(Source.source_type))
        .join(Event, Event.source_id == Source.source_id)
        .where(active)
        .order_by(Source.source_type)
    ).all()
    categories = session.scalars(
        select(distinct(Topic.category)).where(Topic.is_active.is_(True)).order_by(Topic.category)
    ).all()
    topic_count = session.scalar(
        select(func.count(distinct(EventTopic.topic_id)))
        .join(Event, Event.event_id == EventTopic.event_id)
        .join(Topic, Topic.topic_id == EventTopic.topic_id)
        .where(active, Topic.is_active.is_(True))
    )
    relation_count = session.scalar(
        select(func.count())
        .select_from(TopicRelation)
        .join(Topic, Topic.topic_id == TopicRelation.source_topic_id)
        .where(Topic.is_active.is_(True))
    )
    return CatalogMeta(
        earliest_event_at=earliest,
        latest_event_at=latest,
        source_types=tuple(source_types),
        topic_categories=tuple(categories),
        event_count=int(event_count or 0),
        topic_count=int(topic_count or 0),
        relation_count=int(relation_count or 0),
    )


def search_topics(
    session: Session,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    privacy_level: str = "private",
) -> tuple[TopicSummary, ...]:
    pattern = f"%{_escape_like(query.strip())}%"
    statement = (
        select(
            Topic.topic_id,
            Topic.name,
            Topic.category,
            func.count(distinct(Event.event_id)),
            func.count(distinct(func.coalesce(Event.context_id, Event.event_id))),
            func.min(Event.timestamp_start),
            func.max(Event.timestamp_start),
        )
        .join(EventTopic, EventTopic.topic_id == Topic.topic_id)
        .join(Event, Event.event_id == EventTopic.event_id)
        .outerjoin(TopicTerm, TopicTerm.topic_id == Topic.topic_id)
        .outerjoin(CandidateTerm, CandidateTerm.term_id == TopicTerm.term_id)
        .where(
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
            Topic.is_active.is_(True),
            or_(
                Topic.name.ilike(pattern, escape="\\"),
                CandidateTerm.normalized_term.ilike(pattern, escape="\\"),
            ),
        )
        .group_by(Topic.topic_id, Topic.name, Topic.category)
        .order_by(func.count(distinct(Event.event_id)).desc(), Topic.name)
        .limit(limit)
        .offset(offset)
    )
    return tuple(TopicSummary(*row) for row in session.execute(statement))


def search_terms(
    session: Session,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    privacy_level: str = "private",
) -> tuple[TopicSummary, ...]:
    pattern = f"%{_escape_like(query.strip())}%"
    rows = session.execute(
        select(
            CandidateTerm.term_id,
            CandidateTerm.term,
            func.count(distinct(Event.event_id)),
            func.count(distinct(func.coalesce(Event.context_id, Event.event_id))),
            func.min(Event.timestamp_start),
            func.max(Event.timestamp_start),
        )
        .join(EventCandidateTerm, EventCandidateTerm.term_id == CandidateTerm.term_id)
        .join(Event, Event.event_id == EventCandidateTerm.event_id)
        .where(
            CandidateTerm.is_active.is_(True),
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
            CandidateTerm.normalized_term.ilike(pattern, escape="\\"),
        )
        .group_by(CandidateTerm.term_id, CandidateTerm.term)
        .order_by(func.count(distinct(Event.event_id)).desc(), CandidateTerm.term)
        .limit(limit)
        .offset(offset)
    )
    return tuple(
        TopicSummary(
            topic_id=term_id,
            name=term,
            category="term",
            message_count=message_count,
            context_count=context_count,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            layer="terms",
        )
        for term_id, term, message_count, context_count, first_seen, last_seen in rows
    )


def search_catalog(
    session: Session,
    query: str,
    *,
    layer: str = "all",
    limit: int = 20,
    privacy_level: str = "private",
) -> tuple[TopicSummary, ...]:
    values = []
    if layer in {"all", "topics"}:
        values.extend(search_topics(session, query, limit=limit, privacy_level=privacy_level))
    if layer in {"all", "terms"}:
        values.extend(search_terms(session, query, limit=limit, privacy_level=privacy_level))
    values.sort(key=lambda item: (-item.message_count, item.layer, item.name))
    return tuple(values[:limit])


def get_term_detail(
    session: Session,
    term_id: str,
    *,
    privacy_level: str = "private",
    neighbor_limit: int = 12,
) -> TopicDetail | None:
    term = session.get(CandidateTerm, term_id)
    if term is None or not term.is_active:
        return None
    row = session.execute(
        select(
            func.count(distinct(Event.event_id)),
            func.count(distinct(func.coalesce(Event.context_id, Event.event_id))),
            func.min(Event.timestamp_start),
            func.max(Event.timestamp_start),
        )
        .join(EventCandidateTerm, EventCandidateTerm.event_id == Event.event_id)
        .where(
            EventCandidateTerm.term_id == term_id,
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
    ).one()
    summary = TopicSummary(
        topic_id=term.term_id,
        name=term.term,
        category="term",
        message_count=int(row[0] or 0),
        context_count=int(row[1] or 0),
        first_seen_at=row[2],
        last_seen_at=row[3],
        layer="terms",
    )
    return TopicDetail(
        summary=summary,
        months=term_monthly_intensity(session, term_id, privacy_level=privacy_level),
        neighbors=_term_neighbors(
            session,
            term_id,
            target_count=summary.message_count,
            privacy_level=privacy_level,
            limit=neighbor_limit,
        ),
    )


def get_topic_terms(
    session: Session,
    topic_id: str,
    *,
    include_rejected: bool = False,
) -> tuple[TopicTermSummary, ...] | None:
    topic = session.get(Topic, topic_id)
    if topic is None or not topic.is_active:
        return None
    statement = (
        select(
            CandidateTerm.term_id,
            CandidateTerm.term,
            CandidateTerm.ngram_size,
            CandidateTerm.language,
            CandidateTerm.message_count,
            CandidateTerm.context_count,
            CandidateTerm.document_frequency,
            CandidateTerm.tfidf_score,
            CandidateTerm.quality_score,
            CandidateTerm.quality_status,
            CandidateTerm.rejection_reason,
            TopicTerm.relation_type,
        )
        .join(TopicTerm, TopicTerm.term_id == CandidateTerm.term_id)
        .where(TopicTerm.topic_id == topic_id)
        .order_by(CandidateTerm.quality_score.desc(), CandidateTerm.term)
    )
    if not include_rejected:
        statement = statement.where(CandidateTerm.quality_status != "rejected")
    return tuple(TopicTermSummary(*row) for row in session.execute(statement))


def get_topic_detail(
    session: Session,
    topic_id: str,
    *,
    privacy_level: str = "private",
    neighbor_limit: int = 12,
) -> TopicDetail | None:
    topic = session.get(Topic, topic_id)
    if topic is None or not topic.is_active:
        return None

    summary_row = session.execute(
        select(
            func.count(distinct(Event.event_id)),
            func.count(distinct(func.coalesce(Event.context_id, Event.event_id))),
            func.min(Event.timestamp_start),
            func.max(Event.timestamp_start),
        )
        .join(EventTopic, EventTopic.event_id == Event.event_id)
        .where(
            EventTopic.topic_id == topic_id,
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
    ).one()
    summary = TopicSummary(
        topic_id=topic.topic_id,
        name=topic.name,
        category=topic.category,
        message_count=int(summary_row[0] or 0),
        context_count=int(summary_row[1] or 0),
        first_seen_at=summary_row[2],
        last_seen_at=summary_row[3],
    )
    neighbors = _topic_neighbors(
        session,
        topic_id,
        target_count=summary.message_count,
        privacy_level=privacy_level,
        limit=neighbor_limit,
    )
    return TopicDetail(
        summary=summary,
        months=topic_monthly_intensity(session, topic_id, privacy_level=privacy_level),
        neighbors=neighbors,
    )


def get_topic_occurrences(
    session: Session,
    topic_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: str = "private",
    limit: int = 20,
    offset: int = 0,
) -> EventExcerptPage | None:
    topic = session.get(Topic, topic_id)
    if topic is None or not topic.is_active:
        return None
    filters = [
        EventTopic.topic_id == topic_id,
        Event.is_active.is_(True),
        Event.privacy_level == privacy_level,
    ]
    if start is not None:
        filters.append(Event.timestamp_start >= start)
    if end is not None:
        filters.append(Event.timestamp_start < end)
    if source_type is not None:
        filters.append(Source.source_type == source_type)

    base = (
        select(
            Event.event_id,
            Event.timestamp_start,
            ChatGPTMessageModel.role,
            Event.title,
            Event.text,
            Event.source_record_id,
        )
        .join(EventTopic, EventTopic.event_id == Event.event_id)
        .join(Source, Source.source_id == Event.source_id)
        .outerjoin(ChatGPTMessageModel, ChatGPTMessageModel.event_id == Event.event_id)
        .where(*filters)
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(Event.timestamp_start.desc(), Event.event_id).limit(limit).offset(offset)
    )
    items = tuple(
        EventExcerpt(event_id, occurred_at, role, title, _snippet(body), source_record_id)
        for event_id, occurred_at, role, title, body, source_record_id in rows
    )
    return EventExcerptPage(items=items, total=int(total), limit=limit, offset=offset)


def get_term_occurrences(
    session: Session,
    term_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: str = "private",
    limit: int = 20,
    offset: int = 0,
) -> EventExcerptPage | None:
    term = session.get(CandidateTerm, term_id)
    if term is None or not term.is_active:
        return None
    filters = [
        EventCandidateTerm.term_id == term_id,
        Event.is_active.is_(True),
        Event.privacy_level == privacy_level,
    ]
    if start is not None:
        filters.append(Event.timestamp_start >= start)
    if end is not None:
        filters.append(Event.timestamp_start < end)
    if source_type is not None:
        filters.append(Source.source_type == source_type)
    base = (
        select(
            Event.event_id,
            Event.timestamp_start,
            ChatGPTMessageModel.role,
            Event.title,
            Event.text,
            Event.source_record_id,
        )
        .join(EventCandidateTerm, EventCandidateTerm.event_id == Event.event_id)
        .join(Source, Source.source_id == Event.source_id)
        .outerjoin(ChatGPTMessageModel, ChatGPTMessageModel.event_id == Event.event_id)
        .where(*filters)
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        base.order_by(Event.timestamp_start.desc(), Event.event_id).limit(limit).offset(offset)
    )
    items = tuple(
        EventExcerpt(event_id, occurred_at, role, title, _snippet(body), source_record_id)
        for event_id, occurred_at, role, title, body, source_record_id in rows
    )
    return EventExcerptPage(items=items, total=int(total), limit=limit, offset=offset)


def search_events(
    session: Session,
    query: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: str = "private",
    limit: int = 20,
    offset: int = 0,
) -> EventExcerptPage:
    match_query = _fts_query(query)
    filters = ["events.is_active = 1", "events.privacy_level = :privacy_level"]
    parameters: dict[str, object] = {"query": match_query, "privacy_level": privacy_level}
    if start is not None:
        filters.append("events.timestamp_start >= :start")
        parameters["start"] = start
    if end is not None:
        filters.append("events.timestamp_start < :end")
        parameters["end"] = end
    if source_type is not None:
        filters.append("sources.source_type = :source_type")
        parameters["source_type"] = source_type
    where = " AND ".join(filters)
    from_clause = (
        "events_fts JOIN events ON events.rowid = events_fts.rowid "
        "JOIN sources ON sources.source_id = events.source_id "
        "LEFT JOIN chatgpt_messages ON chatgpt_messages.event_id = events.event_id"
    )
    total = session.scalar(
        text(f"SELECT count(*) FROM {from_clause} WHERE events_fts MATCH :query AND {where}"),
        parameters,
    )
    parameters.update({"limit": limit, "offset": offset})
    rows = session.execute(
        text(
            "SELECT events.event_id, events.timestamp_start, chatgpt_messages.role, "
            "events.title, snippet(events_fts, 1, '', '', ' … ', 32), "
            f"events.source_record_id FROM {from_clause} "
            f"WHERE events_fts MATCH :query AND {where} "
            "ORDER BY bm25(events_fts), events.timestamp_start DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        parameters,
    )
    items = tuple(
        EventExcerpt(event_id, occurred_at, role, title, _snippet(body), source_record_id)
        for event_id, occurred_at, role, title, body, source_record_id in rows
    )
    return EventExcerptPage(items=items, total=int(total or 0), limit=limit, offset=offset)


def _topic_neighbors(
    session: Session,
    topic_id: str,
    *,
    target_count: int,
    privacy_level: str,
    limit: int,
) -> tuple[TopicNeighbor, ...]:
    target_events = (
        select(EventTopic.event_id)
        .join(Event)
        .where(
            EventTopic.topic_id == topic_id,
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
    )
    rows = session.execute(
        select(
            Topic.topic_id,
            Topic.name,
            Topic.category,
            Event.event_id,
            func.coalesce(Event.context_id, Event.event_id),
        )
        .join(EventTopic, EventTopic.topic_id == Topic.topic_id)
        .join(Event, Event.event_id == EventTopic.event_id)
        .where(
            EventTopic.event_id.in_(target_events),
            EventTopic.topic_id != topic_id,
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
            Topic.is_active.is_(True),
        )
    )
    metadata: dict[str, tuple[str, str]] = {}
    messages: Counter[str] = Counter()
    contexts: dict[str, set[str]] = defaultdict(set)
    for neighbor_id, name, category, event_id, context_id in rows:
        metadata[neighbor_id] = (name, category)
        messages[neighbor_id] += 1
        contexts[neighbor_id].add(context_id)

    neighbor_totals = dict(
        session.execute(
            select(EventTopic.topic_id, func.count(distinct(EventTopic.event_id)))
            .join(Event, Event.event_id == EventTopic.event_id)
            .where(
                EventTopic.topic_id.in_(messages),
                Event.is_active.is_(True),
                Event.privacy_level == privacy_level,
            )
            .group_by(EventTopic.topic_id)
        ).all()
    )
    result = []
    for neighbor_id, message_count in messages.items():
        denominator = math.sqrt(target_count * neighbor_totals.get(neighbor_id, 0))
        name, category = metadata[neighbor_id]
        result.append(
            TopicNeighbor(
                topic_id=neighbor_id,
                name=name,
                category=category,
                message_count=message_count,
                context_count=len(contexts[neighbor_id]),
                weight=message_count / denominator if denominator else 0.0,
            )
        )
    return tuple(sorted(result, key=lambda item: (-item.weight, item.name))[:limit])


def _term_neighbors(
    session: Session,
    term_id: str,
    *,
    target_count: int,
    privacy_level: str,
    limit: int,
) -> tuple[TopicNeighbor, ...]:
    target_events = (
        select(EventCandidateTerm.event_id)
        .join(Event, Event.event_id == EventCandidateTerm.event_id)
        .where(
            EventCandidateTerm.term_id == term_id,
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
    )
    rows = session.execute(
        select(
            CandidateTerm.term_id,
            CandidateTerm.term,
            Event.event_id,
            func.coalesce(Event.context_id, Event.event_id),
        )
        .join(EventCandidateTerm, EventCandidateTerm.term_id == CandidateTerm.term_id)
        .join(Event, Event.event_id == EventCandidateTerm.event_id)
        .where(
            EventCandidateTerm.event_id.in_(target_events),
            EventCandidateTerm.term_id != term_id,
            CandidateTerm.is_active.is_(True),
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
    )
    names: dict[str, str] = {}
    messages: Counter[str] = Counter()
    contexts: dict[str, set[str]] = defaultdict(set)
    for neighbor_id, name, event_id, context_id in rows:
        names[neighbor_id] = name
        messages[neighbor_id] += 1
        contexts[neighbor_id].add(context_id)
    totals = dict(
        session.execute(
            select(
                EventCandidateTerm.term_id,
                func.count(distinct(EventCandidateTerm.event_id)),
            )
            .join(Event, Event.event_id == EventCandidateTerm.event_id)
            .where(
                EventCandidateTerm.term_id.in_(messages),
                Event.is_active.is_(True),
                Event.privacy_level == privacy_level,
            )
            .group_by(EventCandidateTerm.term_id)
        ).all()
    )
    result = []
    for neighbor_id, message_count in messages.items():
        denominator = math.sqrt(target_count * totals.get(neighbor_id, 0))
        result.append(
            TopicNeighbor(
                topic_id=neighbor_id,
                name=names[neighbor_id],
                category="term",
                message_count=message_count,
                context_count=len(contexts[neighbor_id]),
                weight=message_count / denominator if denominator else 0.0,
            )
        )
    return tuple(sorted(result, key=lambda item: (-item.weight, item.name))[:limit])


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fts_query(value: str) -> str:
    tokens = _SEARCH_TOKEN.findall(value.casefold())
    if not tokens:
        raise ValueError("search_query_has_no_terms")
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _snippet(value: str | None) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= _SNIPPET_LENGTH:
        return normalized
    return f"{normalized[: _SNIPPET_LENGTH - 1].rstrip()}…"
