from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from backend.app.models import Event, EventTopic, Topic, TopicRelation

_WORD_PATTERN = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_STOPWORDS = frozenset(
    """
    a about after again against all also am an and any are as at be because been before being
    between both but by can could did do does doing down during each few for from further had
    has have having he her here hers herself him himself his how i if in into is it its itself
    just me more most my myself no nor not now of off on once only or other our ours ourselves
    out over own same she should so some such than that the their theirs them themselves then
    there these they this those through to too under until up very was we were what when where
    which while who whom why will with would you your yours yourself yourselves
    
    aby albo ale ani aż bez bo być ci co czy dla do gdy gdzie go i ich im inna inne jest jeśli
    już kiedy kto która które który ma mi mnie może na nad nam nas nie niż o od oraz po pod przez
    przy są się ta tak także tam te tego tej ten to tu tych tym tylko w we więc z za ze że
    
    alla allt att av blev bli blir det detta du då en ett eller för från ha hade han har hon hur
    här i inte jag kan med men mot mycket när och om på samma sig sin sina som till under upp ur
    vad var vara vi vid vilken vilka än är även över
    """.split()
)


@dataclass(frozen=True, slots=True)
class TopicBuildResult:
    documents: int
    topics: int
    assignments: int
    relations: int


@dataclass(frozen=True, slots=True)
class MonthlyIntensity:
    month: str
    message_count: int
    weight: float


@dataclass(frozen=True, slots=True)
class _Document:
    event_id: str
    context_id: str
    timestamp: datetime | None
    terms: Counter[str]


def build_topics(
    session: Session,
    *,
    min_document_frequency: int = 5,
    max_topics: int = 500,
    topics_per_event: int = 5,
) -> TopicBuildResult:
    """Build a deterministic local keyword graph from analysis-enabled events."""

    if min_document_frequency < 1 or max_topics < 1 or topics_per_event < 1:
        raise ValueError("topic_build_limits_must_be_positive")

    rows = session.execute(
        select(Event.event_id, Event.context_id, Event.timestamp_start, Event.text).where(
            Event.is_active.is_(True),
            Event.analysis_enabled.is_(True),
            Event.text.is_not(None),
        )
    )
    documents: list[_Document] = []
    document_frequency: Counter[str] = Counter()
    for event_id, context_id, timestamp, text in rows:
        terms = _term_counts(text)
        if not terms:
            continue
        document = _Document(event_id, context_id or event_id, timestamp, terms)
        documents.append(document)
        document_frequency.update(terms)

    selected_terms = {
        term
        for term, _frequency in sorted(
            (
                (term, frequency)
                for term, frequency in document_frequency.items()
                if frequency >= min_document_frequency
            ),
            key=lambda item: (-item[1], -item[0].count(" "), item[0]),
        )[:max_topics]
    }

    session.execute(delete(TopicRelation))
    session.execute(delete(EventTopic))
    session.execute(delete(Topic).where(Topic.category == "keyword"))
    if not selected_terms:
        session.commit()
        return TopicBuildResult(len(documents), 0, 0, 0)

    topic_ids = {term: _topic_id(term) for term in selected_terms}
    topic_documents: Counter[str] = Counter()
    topic_contexts: dict[str, set[str]] = defaultdict(set)
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    assignments: list[dict[str, object]] = []
    document_topics: list[tuple[_Document, tuple[str, ...]]] = []
    document_count = len(documents)

    for document in documents:
        ranked = []
        for term, frequency in document.terms.items():
            if term not in selected_terms:
                continue
            inverse_frequency = (
                math.log((document_count + 1) / (document_frequency[term] + 1)) + 1.0
            )
            ngram_bonus = 1.2 if " " in term else 1.0
            ranked.append((frequency * inverse_frequency * ngram_bonus, term))
        chosen = tuple(
            term
            for _score, term in sorted(ranked, key=lambda item: (-item[0], item[1]))[
                :topics_per_event
            ]
        )
        if not chosen:
            continue
        document_topics.append((document, chosen))
        for term in chosen:
            topic_id = topic_ids[term]
            score = next(score for score, candidate in ranked if candidate == term)
            assignments.append(
                {"event_id": document.event_id, "topic_id": topic_id, "weight": score}
            )
            topic_documents[term] += 1
            topic_contexts[term].add(document.context_id)
            if document.timestamp is not None:
                first_seen[term] = min(first_seen.get(term, document.timestamp), document.timestamp)
                last_seen[term] = max(last_seen.get(term, document.timestamp), document.timestamp)

    topic_rows = [
        {
            "topic_id": topic_ids[term],
            "name": term,
            "category": "keyword",
            "message_count": topic_documents[term],
            "conversation_count": len(topic_contexts[term]),
            "first_seen_at": first_seen.get(term),
            "last_seen_at": last_seen.get(term),
        }
        for term in sorted(selected_terms)
        if topic_documents[term]
    ]
    if topic_rows:
        session.execute(insert(Topic), topic_rows)
    if assignments:
        session.execute(insert(EventTopic), assignments)

    message_pairs: Counter[tuple[str, str]] = Counter()
    pair_contexts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for document, terms in document_topics:
        for first, second in combinations(sorted(set(terms)), 2):
            pair = (topic_ids[first], topic_ids[second])
            message_pairs[pair] += 1
            pair_contexts[pair].add(document.context_id)

    relation_rows = []
    topic_frequency_by_id = {topic_ids[term]: topic_documents[term] for term in selected_terms}
    for (source_topic_id, target_topic_id), message_count in sorted(message_pairs.items()):
        denominator = math.sqrt(
            topic_frequency_by_id[source_topic_id] * topic_frequency_by_id[target_topic_id]
        )
        relation_rows.append(
            {
                "relation_id": _relation_id(source_topic_id, target_topic_id),
                "source_topic_id": source_topic_id,
                "target_topic_id": target_topic_id,
                "message_count": message_count,
                "conversation_count": len(pair_contexts[(source_topic_id, target_topic_id)]),
                "weight": message_count / denominator if denominator else 0.0,
            }
        )
    if relation_rows:
        session.execute(insert(TopicRelation), relation_rows)
    session.commit()
    return TopicBuildResult(
        documents=len(documents),
        topics=len(topic_rows),
        assignments=len(assignments),
        relations=len(relation_rows),
    )


def topic_monthly_intensity(
    session: Session,
    topic_id: str,
    *,
    privacy_level: str = "private",
) -> tuple[MonthlyIntensity, ...]:
    rows = session.execute(
        select(
            func.strftime("%Y-%m", Event.timestamp_start).label("month"),
            func.count(EventTopic.event_id),
            func.sum(EventTopic.weight),
        )
        .join(EventTopic, EventTopic.event_id == Event.event_id)
        .where(
            EventTopic.topic_id == topic_id,
            Event.is_active.is_(True),
            Event.privacy_level == privacy_level,
            Event.timestamp_start.is_not(None),
        )
        .group_by("month")
        .order_by("month")
    )
    return tuple(
        MonthlyIntensity(month=month, message_count=count, weight=float(weight or 0.0))
        for month, count, weight in rows
    )


def _term_counts(text: str) -> Counter[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = [token for token in _WORD_PATTERN.findall(normalized) if token not in _STOPWORDS]
    terms: Counter[str] = Counter(tokens)
    terms.update(
        f"{first} {second}"
        for first, second in zip(tokens, tokens[1:], strict=False)
        if first != second
    )
    return terms


def _topic_id(term: str) -> str:
    return f"topic-{hashlib.sha256(term.encode()).hexdigest()}"


def _relation_id(source_topic_id: str, target_topic_id: str) -> str:
    value = f"{source_topic_id}:{target_topic_id}"
    return f"topic-relation-{hashlib.sha256(value.encode()).hexdigest()}"
