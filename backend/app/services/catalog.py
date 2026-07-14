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
    CandidateTermRelation,
    ChatGPTMessageModel,
    Event,
    EventCandidateTerm,
    EventTopic,
    Source,
    Topic,
    TopicAlias,
    TopicRelation,
    TopicTerm,
)
from backend.app.services.topics import (
    MonthlyIntensity,
    term_monthly_intensity,
    topic_monthly_intensity,
)
from backend.app.services.analysis_runs import active_analysis_run_subquery

_SNIPPET_LENGTH = 280
_SEARCH_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CatalogMeta:
    earliest_event_at: datetime | None
    latest_event_at: datetime | None
    source_types: tuple[str, ...]
    topic_categories: tuple[str, ...]
    event_count: int
    candidate_term_count: int
    topic_count: int
    candidate_term_relation_count: int
    topic_relation_count: int


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
    match_type: str | None = None


@dataclass(frozen=True, slots=True)
class EventExcerptPage:
    items: tuple[EventExcerpt, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class SearchResolution:
    match_kind: str
    item: TopicSummary


@dataclass(frozen=True, slots=True)
class ExploreMatch:
    item_id: str
    name: str
    layer: str
    message_count: int
    context_count: int


@dataclass(frozen=True, slots=True)
class ExploreResult:
    query: str
    normalized_query: str
    matched_terms: tuple[ExploreMatch, ...]
    matched_topics: tuple[ExploreMatch, ...]
    unique_event_count: int
    unique_context_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    months: tuple[MonthlyIntensity, ...]
    neighbors: tuple[TopicNeighbor, ...]
    occurrences: EventExcerptPage


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
        select(distinct(Topic.category))
        .where(
            Topic.is_active.is_(True),
            Topic.analysis_run_id == active_analysis_run_subquery(),
        )
        .order_by(Topic.category)
    ).all()
    topic_count = session.scalar(
        select(func.count(distinct(EventTopic.topic_id)))
        .join(Event, Event.event_id == EventTopic.event_id)
        .join(Topic, Topic.topic_id == EventTopic.topic_id)
        .where(
            active,
            Topic.is_active.is_(True),
            Topic.analysis_run_id == active_analysis_run_subquery(),
        )
    )
    candidate_term_count = session.scalar(
        select(func.count())
        .select_from(CandidateTerm)
        .where(
            CandidateTerm.is_active.is_(True),
            CandidateTerm.analysis_run_id == active_analysis_run_subquery(),
        )
    )
    candidate_term_relation_count = session.scalar(
        select(func.count())
        .select_from(CandidateTermRelation)
        .where(CandidateTermRelation.analysis_run_id == active_analysis_run_subquery())
    )
    topic_relation_count = session.scalar(
        select(func.count())
        .select_from(TopicRelation)
        .join(Topic, Topic.topic_id == TopicRelation.source_topic_id)
        .where(
            Topic.is_active.is_(True),
            Topic.analysis_run_id == active_analysis_run_subquery(),
        )
    )
    return CatalogMeta(
        earliest_event_at=earliest,
        latest_event_at=latest,
        source_types=tuple(source_types),
        topic_categories=tuple(categories),
        event_count=int(event_count or 0),
        candidate_term_count=int(candidate_term_count or 0),
        topic_count=int(topic_count or 0),
        candidate_term_relation_count=int(candidate_term_relation_count or 0),
        topic_relation_count=int(topic_relation_count or 0),
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
        .outerjoin(TopicAlias, TopicAlias.topic_id == Topic.topic_id)
        .where(
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
            Topic.is_active.is_(True),
            Topic.analysis_run_id == active_analysis_run_subquery(),
            or_(
                Topic.name.ilike(pattern, escape="\\"),
                TopicAlias.normalized_alias.ilike(pattern, escape="\\"),
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
            CandidateTerm.analysis_run_id == active_analysis_run_subquery(),
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


def resolve_search_query(
    session: Session,
    query: str,
    *,
    privacy_level: str = "private",
) -> SearchResolution | None:
    """Resolve exact concepts before aliases, terms and prefix matches."""

    normalized = query.strip().casefold()
    if not normalized:
        return None
    topics = search_topics(session, query, limit=100, privacy_level=privacy_level)
    terms = search_terms(session, query, limit=100, privacy_level=privacy_level)
    exact_topic = next((item for item in topics if item.name.casefold() == normalized), None)
    if exact_topic:
        return SearchResolution("exact_topic", exact_topic)
    alias_topic_id = session.scalar(
        select(TopicAlias.topic_id)
        .join(Topic, Topic.topic_id == TopicAlias.topic_id)
        .where(
            TopicAlias.normalized_alias == normalized,
            Topic.is_active.is_(True),
            Topic.analysis_run_id == active_analysis_run_subquery(),
        )
        .limit(1)
    )
    exact_alias = next((item for item in topics if item.topic_id == alias_topic_id), None)
    if exact_alias is None and alias_topic_id is not None:
        detail = get_topic_detail(session, alias_topic_id, privacy_level=privacy_level)
        exact_alias = detail.summary if detail else None
    if exact_alias:
        return SearchResolution("exact_alias", exact_alias)
    exact_term = next((item for item in terms if item.name.casefold() == normalized), None)
    if exact_term:
        return SearchResolution("exact_term", exact_term)
    if topics:
        return SearchResolution("prefix_topic_or_alias", topics[0])
    if terms:
        return SearchResolution("prefix_term", terms[0])
    return None


def explore_search_query(
    session: Session,
    query: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    content_scope: str = "prose",
    privacy_level: str = "private",
    limit: int = 20,
    offset: int = 0,
    neighbor_limit: int = 12,
) -> ExploreResult:
    normalized = " ".join(query.casefold().split())
    query_tokens = tuple(_SEARCH_TOKEN.findall(normalized))
    if not query_tokens:
        raise ValueError("search_query_has_no_terms")

    active_run = active_analysis_run_subquery()
    term_rows = session.execute(
        select(
            CandidateTerm.term_id,
            CandidateTerm.term,
            CandidateTerm.message_count,
            CandidateTerm.context_count,
        ).where(
            CandidateTerm.is_active.is_(True),
            CandidateTerm.analysis_run_id == active_run,
        )
    )
    matched_terms = tuple(
        ExploreMatch(term_id, name, "terms", message_count, context_count)
        for term_id, name, message_count, context_count in term_rows
        if _phrase_matches_query(name, query_tokens)
    )
    matched_term_ids = {item.item_id for item in matched_terms}

    topic_rows = session.execute(
        select(
            Topic.topic_id,
            Topic.name,
            Topic.message_count,
            Topic.conversation_count,
            TopicAlias.normalized_alias,
        )
        .outerjoin(TopicAlias, TopicAlias.topic_id == Topic.topic_id)
        .where(Topic.is_active.is_(True), Topic.analysis_run_id == active_run)
    )
    topic_matches: dict[str, ExploreMatch] = {}
    for topic_id, name, message_count, context_count, alias in topic_rows:
        if _phrase_matches_query(name, query_tokens) or (
            alias and _phrase_matches_query(alias, query_tokens)
        ):
            topic_matches[topic_id] = ExploreMatch(
                topic_id, name, "topics", message_count, context_count
            )
    matched_topics = tuple(topic_matches.values())
    matched_topic_ids = set(topic_matches)

    event_ids: set[str] = set()
    if matched_term_ids:
        event_ids.update(
            session.scalars(
                select(EventCandidateTerm.event_id).where(
                    EventCandidateTerm.term_id.in_(matched_term_ids),
                    EventCandidateTerm.analysis_run_id == active_run,
                )
            )
        )
    if matched_topic_ids:
        event_ids.update(
            session.scalars(
                select(EventTopic.event_id).where(
                    EventTopic.topic_id.in_(matched_topic_ids),
                    EventTopic.analysis_run_id == active_run,
                )
            )
        )
    scope_types = _explore_scope_types(content_scope)
    event_ids.update(
        row[0]
        for row in session.execute(
            text(
                "SELECT DISTINCT event_segments.event_id FROM event_segments_fts "
                "JOIN event_segments ON event_segments.rowid=event_segments_fts.rowid "
                "WHERE event_segments_fts MATCH :query "
                "AND event_segments.analysis_run_id=(SELECT analysis_run_id FROM analysis_runs "
                "WHERE is_active=1 AND status='completed' LIMIT 1) "
                "AND event_segments.segment_type IN ("
                + ",".join(f"'{value}'" for value in scope_types)
                + ")"
            ),
            {"query": _fts_query(normalized)},
        )
    )

    event_statement = (
        select(Event.event_id, Event.context_id, Event.timestamp_start)
        .join(Source, Source.source_id == Event.source_id)
        .where(
            Event.event_id.in_(event_ids or {"__none__"}),
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
    )
    if start is not None:
        event_statement = event_statement.where(Event.timestamp_start >= start)
    if end is not None:
        event_statement = event_statement.where(Event.timestamp_start < end)
    if source_type is not None:
        event_statement = event_statement.where(Source.source_type == source_type)
    event_rows = session.execute(event_statement).all()
    filtered_event_ids = {row[0] for row in event_rows}
    contexts = {context_id or event_id for event_id, context_id, _timestamp in event_rows}
    timestamps = [timestamp for _event_id, _context_id, timestamp in event_rows if timestamp]
    month_counts = Counter(timestamp.strftime("%Y-%m") for timestamp in timestamps)

    occurrence_rows = session.execute(
        select(
            Event.event_id,
            Event.timestamp_start,
            ChatGPTMessageModel.role,
            Event.title,
            Event.text,
            Event.source_record_id,
        )
        .outerjoin(ChatGPTMessageModel, ChatGPTMessageModel.event_id == Event.event_id)
        .where(Event.event_id.in_(filtered_event_ids or {"__none__"}))
        .order_by(Event.timestamp_start.desc(), Event.event_id)
        .limit(limit)
        .offset(offset)
    )
    occurrences = EventExcerptPage(
        items=tuple(
            EventExcerpt(event_id, occurred_at, role, title, _snippet(body), source_record_id)
            for event_id, occurred_at, role, title, body, source_record_id in occurrence_rows
        ),
        total=len(filtered_event_ids),
        limit=limit,
        offset=offset,
    )
    return ExploreResult(
        query=query,
        normalized_query=normalized,
        matched_terms=tuple(
            sorted(matched_terms, key=lambda item: (-item.message_count, item.name))
        ),
        matched_topics=tuple(
            sorted(matched_topics, key=lambda item: (-item.message_count, item.name))
        ),
        unique_event_count=len(filtered_event_ids),
        unique_context_count=len(contexts),
        first_seen_at=min(timestamps) if timestamps else None,
        last_seen_at=max(timestamps) if timestamps else None,
        months=tuple(
            MonthlyIntensity(month, count, float(count))
            for month, count in sorted(month_counts.items())
        ),
        neighbors=_aggregate_query_neighbors(
            session,
            filtered_event_ids,
            excluded_term_ids=matched_term_ids,
            query_tokens=query_tokens,
            privacy_level=privacy_level,
            limit=neighbor_limit,
        ),
        occurrences=occurrences,
    )


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
    content_scope: str = "all",
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
    scope_types = {
        "all": None,
        "prose": ("prose", "quote"),
        "code": ("code", "inline_code"),
        "commands": ("shell_command",),
        "logs": ("log",),
    }
    if content_scope not in scope_types:
        raise ValueError("invalid_content_scope")
    selected_types = scope_types[content_scope]
    if selected_types:
        placeholders = []
        for index, segment_type in enumerate(selected_types):
            key = f"scope_{index}"
            placeholders.append(f":{key}")
            parameters[key] = segment_type
        filters.append(f"event_segments.segment_type IN ({', '.join(placeholders)})")
    filters.append("event_segments.search_enabled = 1")
    where = " AND ".join(filters)
    from_clause = (
        "event_segments_fts JOIN event_segments "
        "ON event_segments.rowid = event_segments_fts.rowid "
        "JOIN events ON events.event_id = event_segments.event_id "
        "JOIN sources ON sources.source_id = events.source_id "
        "LEFT JOIN chatgpt_messages ON chatgpt_messages.event_id = events.event_id"
    )
    total = session.scalar(
        text(
            f"SELECT count(*) FROM {from_clause} WHERE event_segments_fts MATCH :query AND {where}"
        ),
        parameters,
    )
    parameters.update({"limit": limit, "offset": offset})
    rows = session.execute(
        text(
            "SELECT events.event_id, events.timestamp_start, chatgpt_messages.role, "
            "events.title, snippet(event_segments_fts, 0, '', '', ' … ', 32), "
            f"events.source_record_id, event_segments.segment_type FROM {from_clause} "
            f"WHERE event_segments_fts MATCH :query AND {where} "
            "ORDER BY bm25(event_segments_fts), events.timestamp_start DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        parameters,
    )
    items = tuple(
        EventExcerpt(
            event_id,
            occurred_at,
            role,
            title,
            _snippet(body),
            source_record_id,
            match_type,
        )
        for event_id, occurred_at, role, title, body, source_record_id, match_type in rows
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
    target_events = tuple(
        session.scalars(
            select(EventTopic.event_id)
            .join(Event)
            .where(
                EventTopic.topic_id == topic_id,
                Event.is_active.is_(True),
                Event.privacy_level == privacy_level,
            )
        )
    )
    if not target_events:
        return ()
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
            Topic.analysis_run_id == active_analysis_run_subquery(),
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
    target_events = tuple(
        session.scalars(
            select(EventCandidateTerm.event_id)
            .join(Event, Event.event_id == EventCandidateTerm.event_id)
            .where(
                EventCandidateTerm.term_id == term_id,
                Event.is_active.is_(True),
                Event.privacy_level == privacy_level,
            )
        )
    )
    if not target_events:
        return ()
    rows = session.execute(
        select(
            CandidateTerm.term_id,
            CandidateTerm.term,
            func.count(distinct(Event.event_id)),
            func.count(distinct(func.coalesce(Event.context_id, Event.event_id))),
        )
        .join(EventCandidateTerm, EventCandidateTerm.term_id == CandidateTerm.term_id)
        .join(Event, Event.event_id == EventCandidateTerm.event_id)
        .where(
            EventCandidateTerm.event_id.in_(target_events),
            EventCandidateTerm.term_id != term_id,
            CandidateTerm.is_active.is_(True),
            CandidateTerm.analysis_run_id == active_analysis_run_subquery(),
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
        .group_by(CandidateTerm.term_id, CandidateTerm.term)
        .order_by(func.count(distinct(Event.event_id)).desc(), CandidateTerm.term)
        .limit(limit)
    )
    result = [
        TopicNeighbor(
            topic_id=neighbor_id,
            name=name,
            category="term",
            message_count=message_count,
            context_count=context_count,
            weight=message_count / target_count if target_count else 0.0,
        )
        for neighbor_id, name, message_count, context_count in rows
    ]
    return tuple(result)


def _aggregate_query_neighbors(
    session: Session,
    event_ids: set[str],
    *,
    excluded_term_ids: set[str],
    query_tokens: tuple[str, ...],
    privacy_level: str,
    limit: int,
) -> tuple[TopicNeighbor, ...]:
    if not event_ids:
        return ()
    assignment_rows = session.execute(
        select(
            EventCandidateTerm.term_id,
            Event.event_id,
            func.coalesce(Event.context_id, Event.event_id),
        )
        .select_from(EventCandidateTerm)
        .join(Event, Event.event_id == EventCandidateTerm.event_id)
        .where(
            EventCandidateTerm.event_id.in_(event_ids),
            EventCandidateTerm.analysis_run_id == active_analysis_run_subquery(),
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
        )
    )
    term_events: dict[str, set[str]] = defaultdict(set)
    term_contexts: dict[str, set[str]] = defaultdict(set)
    for term_id, event_id, context_id in assignment_rows:
        term_events[term_id].add(event_id)
        term_contexts[term_id].add(context_id)
    eligible_ids = {
        term_id
        for term_id, contexts in term_contexts.items()
        if len(contexts) >= 2 and term_id not in excluded_term_ids
    }
    if not eligible_ids:
        return ()
    metadata = dict(
        session.execute(
            select(CandidateTerm.term_id, CandidateTerm.term).where(
                CandidateTerm.term_id.in_(eligible_ids),
                CandidateTerm.is_active.is_(True),
                CandidateTerm.analysis_run_id == active_analysis_run_subquery(),
            )
        ).all()
    )
    ranked = sorted(
        metadata,
        key=lambda term_id: (-len(term_events[term_id]), metadata[term_id]),
    )
    result = []
    for term_id in ranked:
        name = metadata[term_id]
        if term_id in excluded_term_ids or _phrase_matches_query(name, query_tokens):
            continue
        message_count = len(term_events[term_id])
        context_count = len(term_contexts[term_id])
        result.append(
            TopicNeighbor(
                topic_id=term_id,
                name=name,
                category="term",
                message_count=message_count,
                context_count=context_count,
                weight=context_count / max(1, len(event_ids)),
            )
        )
        if len(result) >= limit:
            break
    return tuple(result)


def _phrase_matches_query(value: str, query_tokens: tuple[str, ...]) -> bool:
    tokens = tuple(_SEARCH_TOKEN.findall(value.casefold()))
    if not tokens or len(tokens) < len(query_tokens):
        return False
    width = len(query_tokens)
    return any(
        tokens[index : index + width] == query_tokens for index in range(len(tokens) - width + 1)
    )


def _explore_scope_types(content_scope: str) -> tuple[str, ...]:
    scopes = {
        "prose": ("prose", "quote"),
        "code": ("code", "inline_code"),
        "commands": ("shell_command",),
        "logs": ("log",),
        "all": ("prose", "quote", "code", "inline_code", "shell_command", "log"),
    }
    if content_scope not in scopes:
        raise ValueError("invalid_content_scope")
    return scopes[content_scope]


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
