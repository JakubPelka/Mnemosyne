from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Event, EventTopic, Source, Topic


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
        denominator = math.sqrt(len(topic_events[source_id]) * len(topic_events[target_id]))
        weight = message_count / denominator if denominator else 0.0
        if message_count < min_edge_messages or weight < min_relation_weight:
            continue
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
