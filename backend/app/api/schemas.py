from datetime import datetime

from pydantic import BaseModel, Field


class GraphNodeResponse(BaseModel):
    topic_id: str
    name: str
    category: str
    message_count: int
    context_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None


class GraphEdgeResponse(BaseModel):
    source_topic_id: str
    target_topic_id: str
    message_count: int
    context_count: int
    weight: float


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


class MonthlyIntensityResponse(BaseModel):
    month: str
    message_count: int
    weight: float


class TopicIntensityResponse(BaseModel):
    topic_id: str
    months: list[MonthlyIntensityResponse]


class ContextMessageResponse(BaseModel):
    event_id: str
    message_id: str
    role: str
    created_at: datetime | None
    text: str | None
    sequence_number: int
    is_target: bool


class MessageContextResponse(BaseModel):
    conversation_id: str
    conversation_title: str | None
    messages: list[ContextMessageResponse]


class MetaCountsResponse(BaseModel):
    events: int
    topics: int
    relations: int


class MetaResponse(BaseModel):
    earliest_event_at: datetime | None
    latest_event_at: datetime | None
    source_types: list[str]
    topic_categories: list[str]
    counts: MetaCountsResponse
    api_version: str


class TopicSearchItemResponse(BaseModel):
    topic_id: str
    name: str
    category: str
    message_count: int
    context_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None


class TopicSearchResponse(BaseModel):
    items: list[TopicSearchItemResponse]


class TopicTermResponse(BaseModel):
    term_id: str
    term: str
    ngram_size: int = Field(ge=1, le=3)
    language: str | None
    message_count: int
    context_count: int
    document_frequency: int
    tfidf_score: float
    quality_score: float
    quality_status: str
    rejection_reason: str | None
    relation_type: str


class TopicTermsResponse(BaseModel):
    topic_id: str
    items: list[TopicTermResponse]


class TopicNeighborResponse(BaseModel):
    topic_id: str
    name: str
    category: str
    message_count: int
    context_count: int
    weight: float


class TopicDetailResponse(BaseModel):
    topic_id: str
    name: str
    category: str
    message_count: int
    context_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    months: list[MonthlyIntensityResponse]
    neighbors: list[TopicNeighborResponse]


class EventExcerptResponse(BaseModel):
    event_id: str
    occurred_at: datetime | None
    role: str | None
    conversation_title: str | None
    snippet: str
    source_record_id: str


class PaginatedEventExcerptResponse(BaseModel):
    items: list[EventExcerptResponse]
    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
