from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import (
    CandidateTerm,
    CandidateTermRelation,
    Event,
    EventCandidateTerm,
    EventTopic,
    Source,
    Topic,
    TopicRelation,
)
from backend.app.services.analysis_runs import active_analysis_run_id


@dataclass(frozen=True, slots=True)
class TopicGraphNode:
    topic_id: str
    name: str
    category: str
    message_count: int
    context_count: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class TopicGraphEdge:
    source_topic_id: str
    target_topic_id: str
    message_count: int
    context_count: int
    weight: float


@dataclass(frozen=True, slots=True)
class TopicGraph:
    nodes: tuple[TopicGraphNode, ...]
    edges: tuple[TopicGraphEdge, ...]


def get_topic_graph(
    session: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: str = "private",
    categories: frozenset[str] | None = None,
    min_occurrences: int = 2,
    min_edge_messages: int = 1,
    min_relation_weight: float = 0.0,
    node_limit: int = 100,
    selected_topic_id: str | None = None,
    neighbors_only: bool = False,
) -> TopicGraph:
    if min_occurrences < 1 or min_edge_messages < 1 or node_limit < 1:
        raise ValueError("graph_limits_must_be_positive")
    if not 0.0 <= min_relation_weight <= 1.0:
        raise ValueError("graph_relation_weight_out_of_range")

    analysis_run_id = active_analysis_run_id(session)
    if analysis_run_id is None:
        return TopicGraph(nodes=(), edges=())
    statement = (
        select(
            Event.event_id,
            Event.context_id,
            Event.timestamp_start,
            Topic.topic_id,
            Topic.name,
            Topic.category,
        )
        .join(EventTopic, EventTopic.event_id == Event.event_id)
        .join(Topic, Topic.topic_id == EventTopic.topic_id)
        .join(Source, Source.source_id == Event.source_id)
        .where(
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
            Topic.is_active.is_(True),
            Topic.analysis_run_id == analysis_run_id,
        )
    )
    if start is not None:
        statement = statement.where(Event.timestamp_start >= start)
    if end is not None:
        statement = statement.where(Event.timestamp_start < end)
    if source_type is not None:
        statement = statement.where(Source.source_type == source_type)
    if categories:
        statement = statement.where(Topic.category.in_(categories))

    topic_names: dict[str, tuple[str, str]] = {}
    topic_events: dict[str, set[str]] = defaultdict(set)
    topic_contexts: dict[str, set[str]] = defaultdict(set)
    topic_timestamps: dict[str, list[datetime]] = defaultdict(list)
    event_topics: dict[str, set[str]] = defaultdict(set)
    event_contexts: dict[str, str] = {}

    for event_id, context_id, timestamp, topic_id, name, category in session.execute(statement):
        topic_names[topic_id] = (name, category)
        topic_events[topic_id].add(event_id)
        topic_contexts[topic_id].add(context_id or event_id)
        if timestamp is not None:
            topic_timestamps[topic_id].append(timestamp)
        event_topics[event_id].add(topic_id)
        event_contexts[event_id] = context_id or event_id

    eligible = [
        topic_id for topic_id, events in topic_events.items() if len(events) >= min_occurrences
    ]
    eligible.sort(key=lambda topic_id: (-len(topic_events[topic_id]), topic_names[topic_id][0]))
    visible = eligible[:node_limit]
    if selected_topic_id in eligible and selected_topic_id not in visible:
        visible = [*visible[:-1], selected_topic_id] if visible else [selected_topic_id]
    visible_set = set(visible)

    pair_messages: Counter[tuple[str, str]] = Counter()
    pair_contexts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event_id, assigned_topics in event_topics.items():
        for pair in combinations(sorted(assigned_topics & visible_set), 2):
            pair_messages[pair] += 1
            pair_contexts[pair].add(event_contexts[event_id])

    edges = []
    for (source_id, target_id), message_count in sorted(pair_messages.items()):
        shared_context_count = len(pair_contexts[(source_id, target_id)])
        if shared_context_count < 2:
            continue
        denominator = math.sqrt(len(topic_contexts[source_id]) * len(topic_contexts[target_id]))
        weight = shared_context_count / denominator if denominator else 0.0
        if message_count < min_edge_messages or weight < min_relation_weight:
            continue
        edges.append(
            TopicGraphEdge(
                source_topic_id=source_id,
                target_topic_id=target_id,
                message_count=message_count,
                context_count=shared_context_count,
                weight=weight,
            )
        )

    use_stored_relations = (
        start is None and end is None and source_type is None and privacy_level == "private"
    )
    if use_stored_relations:
        edges = [
            TopicGraphEdge(*row)
            for row in session.execute(
                select(
                    TopicRelation.source_topic_id,
                    TopicRelation.target_topic_id,
                    TopicRelation.message_count,
                    TopicRelation.conversation_count,
                    TopicRelation.weight,
                ).where(
                    TopicRelation.analysis_run_id == analysis_run_id,
                    TopicRelation.source_topic_id.in_(visible_set),
                    TopicRelation.target_topic_id.in_(visible_set),
                    TopicRelation.message_count >= min_edge_messages,
                    TopicRelation.weight >= min_relation_weight,
                )
            )
        ]

    if neighbors_only and selected_topic_id is not None:
        neighbor_ids = {selected_topic_id}
        neighbor_ids.update(
            edge.target_topic_id for edge in edges if edge.source_topic_id == selected_topic_id
        )
        neighbor_ids.update(
            edge.source_topic_id for edge in edges if edge.target_topic_id == selected_topic_id
        )
        visible = [topic_id for topic_id in visible if topic_id in neighbor_ids]
        visible_set = set(visible)
        edges = [
            edge
            for edge in edges
            if edge.source_topic_id in visible_set and edge.target_topic_id in visible_set
        ]

    nodes = []
    for topic_id in visible:
        timestamps = topic_timestamps[topic_id]
        name, category = topic_names[topic_id]
        nodes.append(
            TopicGraphNode(
                topic_id=topic_id,
                name=name,
                category=category,
                message_count=len(topic_events[topic_id]),
                context_count=len(topic_contexts[topic_id]),
                first_seen_at=min(timestamps) if timestamps else None,
                last_seen_at=max(timestamps) if timestamps else None,
            )
        )
    return TopicGraph(nodes=tuple(nodes), edges=tuple(edges))


def get_candidate_term_graph(
    session: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source_type: str | None = None,
    privacy_level: str = "private",
    min_occurrences: int = 2,
    min_edge_messages: int = 1,
    min_relation_weight: float = 0.0,
    node_limit: int = 100,
    selected_term_id: str | None = None,
    neighbors_only: bool = False,
    include_rejected: bool = False,
) -> TopicGraph:
    analysis_run_id = active_analysis_run_id(session)
    if analysis_run_id is None:
        return TopicGraph(nodes=(), edges=())
    event_filters = [(Event.is_active + 0) == 1, Event.privacy_level == privacy_level]
    if start is not None:
        event_filters.append(Event.timestamp_start >= start)
    if end is not None:
        event_filters.append(Event.timestamp_start < end)

    term_filters = [CandidateTerm.analysis_run_id == analysis_run_id]
    if not include_rejected:
        term_filters.append(CandidateTerm.is_active.is_(True))

    if start is None and end is None and source_type is None and privacy_level == "private":
        candidate_filters = [CandidateTerm.analysis_run_id == analysis_run_id]
        if not include_rejected:
            candidate_filters.append(CandidateTerm.is_active.is_(True))
        ranked = session.execute(
            select(CandidateTerm.term_id, CandidateTerm.message_count)
            .where(*candidate_filters, CandidateTerm.message_count >= min_occurrences)
            .order_by(CandidateTerm.message_count.desc(), CandidateTerm.term_id)
            .limit(node_limit)
        ).all()
    else:
        event_stmt = select(Event.event_id, Event.context_id, Event.timestamp_start)
        if source_type is not None:
            event_stmt = event_stmt.join(Source, Source.source_id == Event.source_id).where(
                Source.source_type == source_type
            )
        event_stmt = event_stmt.where(*event_filters)
        filtered_events = event_stmt.cte("filtered_events")

        ranked = session.execute(
            select(
                CandidateTerm.term_id,
                func.count(filtered_events.c.event_id).label("event_count"),
            )
            .select_from(filtered_events)
            .join(EventCandidateTerm, EventCandidateTerm.event_id == filtered_events.c.event_id)
            .join(CandidateTerm, CandidateTerm.term_id == EventCandidateTerm.term_id)
            .where(*term_filters)
            .group_by(CandidateTerm.term_id)
            .having(func.count(filtered_events.c.event_id) >= min_occurrences)
            .order_by(func.count(filtered_events.c.event_id).desc(), CandidateTerm.term_id)
            .limit(node_limit)
        ).all()
    visible_ids = [term_id for term_id, _count in ranked]
    if selected_term_id and selected_term_id not in visible_ids:
        if start is None and end is None and source_type is None and privacy_level == "private":
            selected_count = session.scalar(
                select(CandidateTerm.message_count).where(
                    CandidateTerm.term_id == selected_term_id,
                    CandidateTerm.analysis_run_id == analysis_run_id,
                )
            )
        else:
            selected_count = session.scalar(
                select(func.count(filtered_events.c.event_id))
                .select_from(filtered_events)
                .join(EventCandidateTerm, EventCandidateTerm.event_id == filtered_events.c.event_id)
                .join(CandidateTerm, CandidateTerm.term_id == EventCandidateTerm.term_id)
                .where(*term_filters, CandidateTerm.term_id == selected_term_id)
            )
        if selected_count and selected_count >= min_occurrences:
            visible_ids = [*visible_ids[:-1], selected_term_id]
    if not visible_ids:
        return TopicGraph(nodes=(), edges=())

    if start is None and end is None and source_type is None and privacy_level == "private":
        statement = (
            select(
                Event.event_id,
                Event.context_id,
                Event.timestamp_start,
                CandidateTerm.term_id,
                CandidateTerm.term,
                CandidateTerm.quality_status,
            )
            .select_from(EventCandidateTerm)
            .join(Event, Event.event_id == EventCandidateTerm.event_id)
            .join(CandidateTerm, CandidateTerm.term_id == EventCandidateTerm.term_id)
            .where(
                (Event.is_active + 0) == 1,
                Event.privacy_level == privacy_level,
                *term_filters,
                CandidateTerm.term_id.in_(visible_ids),
            )
        )
    else:
        statement = (
            select(
                filtered_events.c.event_id,
                filtered_events.c.context_id,
                filtered_events.c.timestamp_start,
                CandidateTerm.term_id,
                CandidateTerm.term,
                CandidateTerm.quality_status,
            )
            .select_from(filtered_events)
            .join(EventCandidateTerm, EventCandidateTerm.event_id == filtered_events.c.event_id)
            .join(CandidateTerm, CandidateTerm.term_id == EventCandidateTerm.term_id)
            .where(*term_filters, CandidateTerm.term_id.in_(visible_ids))
        )
    rows = (
        (event_id, context_id, timestamp, term_id, term, f"candidate_{status}")
        for event_id, context_id, timestamp, term_id, term, status in session.execute(statement)
    )
    use_stored_relations = (
        start is None and end is None and source_type is None and privacy_level == "private"
    )
    graph = _assemble_graph(
        rows,
        min_occurrences=min_occurrences,
        min_edge_messages=min_edge_messages,
        min_relation_weight=min_relation_weight,
        node_limit=node_limit,
        selected_topic_id=selected_term_id,
        neighbors_only=neighbors_only and not use_stored_relations,
    )
    if not use_stored_relations:
        return graph
    visible_set = {node.topic_id for node in graph.nodes}
    relation_rows = session.execute(
        select(
            CandidateTermRelation.source_term_id,
            CandidateTermRelation.target_term_id,
            CandidateTermRelation.shared_event_count,
            CandidateTermRelation.shared_context_count,
            CandidateTermRelation.weight,
        ).where(
            CandidateTermRelation.analysis_run_id == analysis_run_id,
            CandidateTermRelation.source_term_id.in_(visible_set),
            CandidateTermRelation.target_term_id.in_(visible_set),
            CandidateTermRelation.shared_event_count >= min_edge_messages,
            CandidateTermRelation.weight >= min_relation_weight,
        )
    )
    edges = tuple(TopicGraphEdge(*row) for row in relation_rows)
    nodes = graph.nodes
    if neighbors_only and selected_term_id is not None:
        neighbor_ids = {selected_term_id}
        for edge in edges:
            if edge.source_topic_id == selected_term_id:
                neighbor_ids.add(edge.target_topic_id)
            if edge.target_topic_id == selected_term_id:
                neighbor_ids.add(edge.source_topic_id)
        nodes = tuple(node for node in nodes if node.topic_id in neighbor_ids)
        node_ids = {node.topic_id for node in nodes}
        edges = tuple(
            edge
            for edge in edges
            if edge.source_topic_id in node_ids and edge.target_topic_id in node_ids
        )
    return TopicGraph(nodes=nodes, edges=edges)


def _assemble_graph(
    rows: object,
    *,
    min_occurrences: int,
    min_edge_messages: int,
    min_relation_weight: float,
    node_limit: int,
    selected_topic_id: str | None,
    neighbors_only: bool,
) -> TopicGraph:
    if min_occurrences < 1 or min_edge_messages < 1 or node_limit < 1:
        raise ValueError("graph_limits_must_be_positive")
    if not 0.0 <= min_relation_weight <= 1.0:
        raise ValueError("graph_relation_weight_out_of_range")

    topic_names: dict[str, tuple[str, str]] = {}
    topic_events: dict[str, set[str]] = defaultdict(set)
    topic_contexts: dict[str, set[str]] = defaultdict(set)
    topic_timestamps: dict[str, list[datetime]] = defaultdict(list)
    event_topics: dict[str, set[str]] = defaultdict(set)
    event_contexts: dict[str, str] = {}
    for event_id, context_id, timestamp, topic_id, name, category in rows:  # type: ignore[union-attr]
        topic_names[topic_id] = (name, category)
        topic_events[topic_id].add(event_id)
        topic_contexts[topic_id].add(context_id or event_id)
        if timestamp is not None:
            topic_timestamps[topic_id].append(timestamp)
        event_topics[event_id].add(topic_id)
        event_contexts[event_id] = context_id or event_id

    eligible = [key for key, events in topic_events.items() if len(events) >= min_occurrences]
    eligible.sort(key=lambda key: (-len(topic_events[key]), topic_names[key][0]))
    visible = eligible[:node_limit]
    if selected_topic_id in eligible and selected_topic_id not in visible:
        visible = [*visible[:-1], selected_topic_id] if visible else [selected_topic_id]
    visible_set = set(visible)
    pair_messages: Counter[tuple[str, str]] = Counter()
    pair_contexts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event_id, assigned_topics in event_topics.items():
        for pair in combinations(sorted(assigned_topics & visible_set), 2):
            pair_messages[pair] += 1
            pair_contexts[pair].add(event_contexts[event_id])
    edges = []
    for (source_id, target_id), message_count in sorted(pair_messages.items()):
        denominator = math.sqrt(len(topic_events[source_id]) * len(topic_events[target_id]))
        weight = message_count / denominator if denominator else 0.0
        if message_count >= min_edge_messages and weight >= min_relation_weight:
            edges.append(
                TopicGraphEdge(
                    source_topic_id=source_id,
                    target_topic_id=target_id,
                    message_count=message_count,
                    context_count=len(pair_contexts[(source_id, target_id)]),
                    weight=weight,
                )
            )
    if neighbors_only and selected_topic_id is not None:
        neighbor_ids = {selected_topic_id}
        for edge in edges:
            if edge.source_topic_id == selected_topic_id:
                neighbor_ids.add(edge.target_topic_id)
            if edge.target_topic_id == selected_topic_id:
                neighbor_ids.add(edge.source_topic_id)
        visible = [key for key in visible if key in neighbor_ids]
        edges = [
            edge
            for edge in edges
            if edge.source_topic_id in neighbor_ids and edge.target_topic_id in neighbor_ids
        ]
    nodes = tuple(
        TopicGraphNode(
            topic_id=key,
            name=topic_names[key][0],
            category=topic_names[key][1],
            message_count=len(topic_events[key]),
            context_count=len(topic_contexts[key]),
            first_seen_at=min(topic_timestamps[key]) if topic_timestamps[key] else None,
            last_seen_at=max(topic_timestamps[key]) if topic_timestamps[key] else None,
        )
        for key in visible
    )
    return TopicGraph(nodes=nodes, edges=tuple(edges))
