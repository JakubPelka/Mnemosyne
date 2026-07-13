from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.api.dependencies import get_session
from backend.app.api.schemas import (
    EventExcerptResponse,
    GraphEdgeResponse,
    GraphNodeResponse,
    GraphResponse,
    MessageContextResponse,
    MetaCountsResponse,
    MetaResponse,
    MonthlyIntensityResponse,
    PaginatedEventExcerptResponse,
    TopicDetailResponse,
    TopicIntensityResponse,
    TopicNeighborResponse,
    TopicSearchItemResponse,
    TopicSearchResponse,
)
from backend.app.main_version import API_VERSION
from backend.app.services.catalog import (
    EventExcerptPage,
    get_catalog_meta,
    get_topic_detail,
    get_topic_occurrences,
    search_events,
    search_topics,
)
from backend.app.services.context import get_message_context
from backend.app.services.graph import get_topic_graph
from backend.app.services.topics import topic_monthly_intensity

router = APIRouter(prefix="/api")
DatabaseSession = Annotated[Session, Depends(get_session)]
PrivacyLevel = Literal["private", "sensitive", "personal", "public"]


@router.get("/meta", response_model=MetaResponse)
def meta(session: DatabaseSession) -> MetaResponse:
    value = get_catalog_meta(session)
    return MetaResponse(
        earliest_event_at=value.earliest_event_at,
        latest_event_at=value.latest_event_at,
        source_types=list(value.source_types),
        topic_categories=list(value.topic_categories),
        counts=MetaCountsResponse(
            events=value.event_count,
            topics=value.topic_count,
            relations=value.relation_count,
        ),
        api_version=API_VERSION,
    )


@router.get("/graph", response_model=GraphResponse)
def graph(
    session: DatabaseSession,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: PrivacyLevel = "private",
    category: Annotated[list[str] | None, Query()] = None,
    min_occurrences: Annotated[int, Query(ge=1)] = 2,
    min_edge_messages: Annotated[int, Query(ge=1)] = 1,
    min_relation_weight: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    node_limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    selected_topic_id: str | None = None,
    neighbors_only: bool = False,
) -> GraphResponse:
    result = get_topic_graph(
        session,
        start=start,
        end=end,
        source_type=source_type,
        privacy_level=privacy_level,
        categories=frozenset(category) if category else None,
        min_occurrences=min_occurrences,
        min_edge_messages=min_edge_messages,
        min_relation_weight=min_relation_weight,
        node_limit=node_limit,
        selected_topic_id=selected_topic_id,
        neighbors_only=neighbors_only,
    )
    return GraphResponse(
        nodes=[GraphNodeResponse(**asdict(node)) for node in result.nodes],
        edges=[GraphEdgeResponse(**asdict(edge)) for edge in result.edges],
    )


@router.get("/topics/search", response_model=TopicSearchResponse)
def topic_search(
    session: DatabaseSession,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    privacy_level: PrivacyLevel = "private",
) -> TopicSearchResponse:
    return TopicSearchResponse(
        items=[
            TopicSearchItemResponse(**asdict(item))
            for item in search_topics(session, q, limit=limit, privacy_level=privacy_level)
        ]
    )


@router.get("/topics/{topic_id}", response_model=TopicDetailResponse)
def topic_detail(
    topic_id: str,
    session: DatabaseSession,
    privacy_level: PrivacyLevel = "private",
) -> TopicDetailResponse:
    detail = get_topic_detail(session, topic_id, privacy_level=privacy_level)
    if detail is None:
        raise HTTPException(status_code=404, detail="topic_not_found")
    return TopicDetailResponse(
        **asdict(detail.summary),
        months=[MonthlyIntensityResponse(**asdict(month)) for month in detail.months],
        neighbors=[TopicNeighborResponse(**asdict(neighbor)) for neighbor in detail.neighbors],
    )


@router.get("/topics/{topic_id}/intensity", response_model=TopicIntensityResponse)
def topic_intensity(
    topic_id: str,
    session: DatabaseSession,
    privacy_level: PrivacyLevel = "private",
) -> TopicIntensityResponse:
    values = topic_monthly_intensity(session, topic_id, privacy_level=privacy_level)
    return TopicIntensityResponse(
        topic_id=topic_id,
        months=[MonthlyIntensityResponse(**asdict(value)) for value in values],
    )


@router.get(
    "/topics/{topic_id}/occurrences",
    response_model=PaginatedEventExcerptResponse,
)
def topic_occurrences(
    topic_id: str,
    session: DatabaseSession,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: PrivacyLevel = "private",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedEventExcerptResponse:
    page = get_topic_occurrences(
        session,
        topic_id,
        start=start,
        end=end,
        source_type=source_type,
        privacy_level=privacy_level,
        limit=limit,
        offset=offset,
    )
    if page is None:
        raise HTTPException(status_code=404, detail="topic_not_found")
    return _event_page_response(page)


@router.get("/search/events", response_model=PaginatedEventExcerptResponse)
def event_search(
    session: DatabaseSession,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: PrivacyLevel = "private",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedEventExcerptResponse:
    try:
        page = search_events(
            session,
            q,
            start=start,
            end=end,
            source_type=source_type,
            privacy_level=privacy_level,
            limit=limit,
            offset=offset,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _event_page_response(page)


@router.get("/messages/{event_id}/context", response_model=MessageContextResponse)
def message_context(
    event_id: str,
    session: DatabaseSession,
    before: Annotated[int, Query(ge=0, le=10)] = 2,
    after: Annotated[int, Query(ge=0, le=10)] = 2,
) -> MessageContextResponse:
    context = get_message_context(session, event_id, before=before, after=after)
    if context is None:
        raise HTTPException(status_code=404, detail="message_not_found")
    return MessageContextResponse(
        conversation_id=context.conversation_id,
        conversation_title=context.conversation_title,
        messages=[asdict(message) for message in context.messages],
    )


def _event_page_response(page: EventExcerptPage) -> PaginatedEventExcerptResponse:
    return PaginatedEventExcerptResponse(
        items=[EventExcerptResponse(**asdict(item)) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
