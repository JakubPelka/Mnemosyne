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
    SearchResolutionResponse,
    TopicDetailResponse,
    TopicIntensityResponse,
    TopicNeighborResponse,
    TopicSearchItemResponse,
    TopicSearchResponse,
    TopicTermResponse,
    TopicTermsResponse,
)
from backend.app.main_version import API_VERSION
from backend.app.services.catalog import (
    EventExcerptPage,
    get_catalog_meta,
    get_term_detail,
    get_term_occurrences,
    get_topic_detail,
    get_topic_occurrences,
    get_topic_terms,
    search_events,
    search_catalog,
    search_topics,
    resolve_search_query,
)
from backend.app.services.context import get_message_context
from backend.app.services.graph import get_candidate_term_graph, get_topic_graph
from backend.app.services.topics import term_monthly_intensity, topic_monthly_intensity

router = APIRouter(prefix="/api")
DatabaseSession = Annotated[Session, Depends(get_session)]
PrivacyLevel = Literal["private", "sensitive", "personal", "public"]
Layer = Literal["terms", "topics"]


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
    layer: Layer = "terms",
    view: Literal["topics", "terms"] | None = None,
    include_rejected: bool = False,
) -> GraphResponse:
    common = dict(
        start=start,
        end=end,
        source_type=source_type,
        privacy_level=privacy_level,
        min_occurrences=min_occurrences,
        min_edge_messages=min_edge_messages,
        min_relation_weight=min_relation_weight,
        node_limit=node_limit,
        neighbors_only=neighbors_only,
    )
    selected_layer = view or layer
    if selected_layer == "terms":
        result = get_candidate_term_graph(
            session,
            **common,
            selected_term_id=selected_topic_id,
            include_rejected=include_rejected,
        )
    else:
        result = get_topic_graph(
            session,
            **common,
            categories=frozenset(category) if category else None,
            selected_topic_id=selected_topic_id,
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
    layer: Literal["terms", "topics", "all"] = "all",
) -> TopicSearchResponse:
    return TopicSearchResponse(
        items=[
            TopicSearchItemResponse(**asdict(item))
            for item in search_catalog(
                session, q, layer=layer, limit=limit, privacy_level=privacy_level
            )
        ]
    )


@router.get("/search/resolve", response_model=SearchResolutionResponse)
def resolve_search(
    session: DatabaseSession,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    privacy_level: PrivacyLevel = "private",
) -> SearchResolutionResponse:
    result = resolve_search_query(session, q, privacy_level=privacy_level)
    if result is None:
        raise HTTPException(status_code=404, detail="search_query_not_found")
    return SearchResolutionResponse(
        match_kind=result.match_kind,
        item=TopicSearchItemResponse(**asdict(result.item)),
    )


@router.get("/topics", response_model=TopicSearchResponse)
def topic_list(
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
    privacy_level: PrivacyLevel = "private",
) -> TopicSearchResponse:
    return TopicSearchResponse(
        items=[
            TopicSearchItemResponse(**asdict(item))
            for item in search_topics(
                session, "", limit=limit, offset=offset, privacy_level=privacy_level
            )
        ]
    )


@router.get("/topics/{topic_id}/terms", response_model=TopicTermsResponse)
def topic_terms(
    topic_id: str,
    session: DatabaseSession,
    include_rejected: bool = False,
) -> TopicTermsResponse:
    terms = get_topic_terms(session, topic_id, include_rejected=include_rejected)
    if terms is None:
        raise HTTPException(status_code=404, detail="topic_not_found")
    return TopicTermsResponse(
        topic_id=topic_id,
        items=[TopicTermResponse(**asdict(item)) for item in terms],
    )


@router.get("/topics/{topic_id}", response_model=TopicDetailResponse)
def topic_detail(
    topic_id: str,
    session: DatabaseSession,
    privacy_level: PrivacyLevel = "private",
    layer: Layer = "topics",
) -> TopicDetailResponse:
    detail = (
        get_term_detail(session, topic_id, privacy_level=privacy_level)
        if layer == "terms"
        else get_topic_detail(session, topic_id, privacy_level=privacy_level)
    )
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
    layer: Layer = "topics",
) -> TopicIntensityResponse:
    values = (
        term_monthly_intensity(session, topic_id, privacy_level=privacy_level)
        if layer == "terms"
        else topic_monthly_intensity(session, topic_id, privacy_level=privacy_level)
    )
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
    layer: Layer = "topics",
) -> PaginatedEventExcerptResponse:
    occurrence_getter = get_term_occurrences if layer == "terms" else get_topic_occurrences
    page = occurrence_getter(
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
    content_scope: Literal["all", "prose", "code", "commands", "logs"] = "all",
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
            content_scope=content_scope,
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
