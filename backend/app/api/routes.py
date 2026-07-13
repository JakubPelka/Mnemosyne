from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.api.dependencies import get_session
from backend.app.services.context import get_message_context
from backend.app.services.graph import get_topic_graph
from backend.app.services.topics import topic_monthly_intensity

router = APIRouter(prefix="/api")
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.get("/graph")
def graph(
    session: DatabaseSession,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    category: Annotated[list[str] | None, Query()] = None,
    min_occurrences: Annotated[int, Query(ge=1)] = 2,
    min_edge_messages: Annotated[int, Query(ge=1)] = 1,
    min_relation_weight: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    node_limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    selected_topic_id: str | None = None,
    neighbors_only: bool = False,
) -> dict[str, object]:
    result = get_topic_graph(
        session,
        start=start,
        end=end,
        source_type=source_type,
        categories=frozenset(category) if category else None,
        min_occurrences=min_occurrences,
        min_edge_messages=min_edge_messages,
        min_relation_weight=min_relation_weight,
        node_limit=node_limit,
        selected_topic_id=selected_topic_id,
        neighbors_only=neighbors_only,
    )
    return {
        "nodes": [
            {
                "topic_id": node.topic_id,
                "name": node.name,
                "category": node.category,
                "message_count": node.message_count,
                "context_count": node.context_count,
                "first_seen_at": node.first_seen_at,
                "last_seen_at": node.last_seen_at,
            }
            for node in result.nodes
        ],
        "edges": [
            {
                "source_topic_id": edge.source_topic_id,
                "target_topic_id": edge.target_topic_id,
                "message_count": edge.message_count,
                "context_count": edge.context_count,
                "weight": edge.weight,
            }
            for edge in result.edges
        ],
    }


@router.get("/topics/{topic_id}/intensity")
def topic_intensity(topic_id: str, session: DatabaseSession) -> dict[str, object]:
    values = topic_monthly_intensity(session, topic_id)
    return {
        "topic_id": topic_id,
        "months": [
            {
                "month": value.month,
                "message_count": value.message_count,
                "weight": value.weight,
            }
            for value in values
        ],
    }


@router.get("/messages/{event_id}/context")
def message_context(
    event_id: str,
    session: DatabaseSession,
    before: Annotated[int, Query(ge=0, le=10)] = 2,
    after: Annotated[int, Query(ge=0, le=10)] = 2,
) -> dict[str, object]:
    context = get_message_context(session, event_id, before=before, after=after)
    if context is None:
        raise HTTPException(status_code=404, detail="message_not_found")
    return {
        "conversation_id": context.conversation_id,
        "conversation_title": context.conversation_title,
        "messages": [
            {
                "event_id": message.event_id,
                "message_id": message.message_id,
                "role": message.role,
                "created_at": message.created_at,
                "text": message.text,
                "sequence_number": message.sequence_number,
                "is_target": message.is_target,
            }
            for message in context.messages
        ],
    }
