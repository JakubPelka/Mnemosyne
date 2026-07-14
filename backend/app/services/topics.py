from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from backend.app.models import (
    CandidateTerm,
    Event,
    EventCandidateTerm,
    EventSegment,
    EventTopic,
    Topic,
    TopicAlias,
    TopicRelation,
    TopicTerm,
)
from backend.app.services.analysis_runs import (
    activate_analysis_run,
    active_analysis_run_subquery,
    start_analysis_run,
)
from backend.app.nlp.lexicons import ALL_STOPWORDS
from backend.app.nlp.quality import (
    TermQuality,
    assess_term,
    normalize_term,
    phrase_suppresses_unigram,
    term_tokens,
)
from backend.app.services.topic_overrides import TopicOverride, load_topic_overrides
from backend.app.services.segments import rebuild_event_segments

_TEXT_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:[-+.#][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True, slots=True)
class TopicBuildResult:
    documents: int
    candidate_terms: int
    accepted_terms: int
    rejected_terms: int
    topics: int
    topic_terms: int
    assignments: int
    relations: int
    rejection_counts: dict[str, int]


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
    acronyms: frozenset[str]


@dataclass(frozen=True, slots=True)
class _CandidateMetrics:
    term: str
    document_frequency: int
    context_count: int
    tfidf_score: float
    quality: TermQuality
    is_acronym: bool
    is_manual: bool


@dataclass(frozen=True, slots=True)
class _TopicDefinition:
    topic_id: str
    name: str
    category: str
    origin: str
    creation_method: str
    primary_term: str
    aliases: tuple[str, ...]


def build_topics(
    session: Session,
    *,
    min_document_frequency: int = 5,
    max_candidate_terms: int = 5000,
    max_topics: int = 200,
    topics_per_event: int = 5,
    candidate_terms_per_event: int = 5,
    include_trigrams: bool = True,
    overrides_path: Path | None = None,
) -> TopicBuildResult:
    """Build explainable candidate terms and a separate local topic layer."""

    if (
        min(
            min_document_frequency,
            max_candidate_terms,
            max_topics,
            topics_per_event,
            candidate_terms_per_event,
        )
        < 1
    ):
        raise ValueError("topic_build_limits_must_be_positive")

    overrides = load_topic_overrides(overrides_path)
    configuration = {
        "min_document_frequency": min_document_frequency,
        "max_candidate_terms": max_candidate_terms,
        "max_topics": max_topics,
        "topics_per_event": topics_per_event,
        "candidate_terms_per_event": candidate_terms_per_event,
        "include_trigrams": include_trigrams,
        "overrides_hash": hashlib.sha256(repr(overrides).encode()).hexdigest(),
    }
    run = start_analysis_run(session, configuration)
    _clear_derived_analysis(session)
    rebuild_event_segments(session, analysis_run_id=run.analysis_run_id)
    documents = _load_documents(session, include_trigrams=include_trigrams)
    document_frequency: Counter[str] = Counter()
    term_contexts: dict[str, set[str]] = defaultdict(set)
    total_term_score: Counter[str] = Counter()
    for document in documents:
        for term in document.terms:
            document_frequency[term] += 1
            term_contexts[term].add(document.context_id)
    acronym_document_frequency: Counter[str] = Counter()
    for document in documents:
        acronym_document_frequency.update(document.acronyms)
    observed_acronyms = set(acronym_document_frequency)

    manual_aliases = {alias for override in overrides for alias in override.aliases}
    ranked_terms = sorted(
        document_frequency,
        key=lambda term: (-document_frequency[term], -term.count(" "), term),
    )[:max_candidate_terms]
    selected_terms = set(ranked_terms) | manual_aliases | observed_acronyms
    document_count = len(documents)
    for document in documents:
        for term, frequency in document.terms.items():
            if term not in selected_terms:
                continue
            total_term_score[term] += frequency * _inverse_document_frequency(
                document_count, document_frequency[term]
            )

    metrics = _candidate_metrics(
        selected_terms,
        document_frequency=document_frequency,
        term_contexts=term_contexts,
        total_term_score=total_term_score,
        document_count=document_count,
        min_document_frequency=min_document_frequency,
        manual_aliases=manual_aliases,
        acronym_document_frequency=acronym_document_frequency,
    )
    _persist_candidates(session, metrics, analysis_run_id=run.analysis_run_id)
    candidate_assignments = _candidate_assignments(
        documents,
        metrics,
        candidate_terms_per_event=candidate_terms_per_event,
    )
    if candidate_assignments:
        session.execute(
            insert(EventCandidateTerm),
            [{**row, "analysis_run_id": run.analysis_run_id} for row in candidate_assignments],
        )

    definitions = _build_topic_definitions(
        metrics,
        overrides=overrides,
        max_topics=max_topics,
        document_count=document_count,
    )
    result = _persist_topic_layer(
        session,
        documents=documents,
        metrics=metrics,
        definitions=definitions,
        topics_per_event=topics_per_event,
        analysis_run_id=run.analysis_run_id,
    )
    activate_analysis_run(session, run)
    session.commit()
    rejection_counts = Counter(
        metric.quality.rejection_reason
        for metric in metrics.values()
        if metric.quality.rejection_reason is not None
    )
    return TopicBuildResult(
        documents=document_count,
        candidate_terms=len(metrics),
        accepted_terms=sum(metric.quality.status == "accepted" for metric in metrics.values()),
        rejected_terms=sum(metric.quality.status == "rejected" for metric in metrics.values()),
        topics=result[0],
        topic_terms=result[1],
        assignments=result[2],
        relations=result[3],
        rejection_counts=dict(sorted(rejection_counts.items())),
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
            EventTopic.analysis_run_id == active_analysis_run_subquery(),
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


def term_monthly_intensity(
    session: Session,
    term_id: str,
    *,
    privacy_level: str = "private",
) -> tuple[MonthlyIntensity, ...]:
    rows = session.execute(
        select(
            func.strftime("%Y-%m", Event.timestamp_start).label("month"),
            func.count(EventCandidateTerm.event_id),
            func.sum(EventCandidateTerm.weight),
        )
        .join(EventCandidateTerm, EventCandidateTerm.event_id == Event.event_id)
        .where(
            EventCandidateTerm.term_id == term_id,
            EventCandidateTerm.analysis_run_id == active_analysis_run_subquery(),
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


def stable_term_id(term: str) -> str:
    normalized = normalize_term(term)
    return f"term-{hashlib.sha256(normalized.encode()).hexdigest()}"


def stable_topic_id(canonical_name: str, *, manual_id: str | None = None) -> str:
    namespace = f"manual:{manual_id}" if manual_id else f"automatic:{canonical_name}"
    return f"topic-{hashlib.sha256(namespace.encode()).hexdigest()}"


def extract_candidate_terms(text: str, *, include_trigrams: bool = True) -> Counter[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = _TEXT_TOKEN_PATTERN.findall(normalized)
    terms: Counter[str] = Counter(tokens)
    terms.update(
        " ".join(tokens[index : index + 2])
        for index in range(max(0, len(tokens) - 1))
        if tokens[index] != tokens[index + 1]
    )
    if include_trigrams:
        terms.update(
            " ".join(tokens[index : index + 3])
            for index in range(max(0, len(tokens) - 2))
            if len(set(tokens[index : index + 3])) > 1
        )
    return terms


def extract_acronyms(text: str) -> frozenset[str]:
    values = re.findall(r"(?<![\w-])[A-ZÅÄÖ]{2,6}(?![\w-])", unicodedata.normalize("NFKC", text))
    return frozenset(
        normalize_term(value) for value in values if value.casefold() not in ALL_STOPWORDS
    )


def _load_documents(session: Session, *, include_trigrams: bool) -> list[_Document]:
    rows = session.execute(
        select(
            Event.event_id,
            Event.context_id,
            Event.timestamp_start,
            EventSegment.text,
            EventSegment.topic_weight,
        )
        .join(EventSegment, EventSegment.event_id == Event.event_id)
        .where(
            Event.is_active.is_(True),
            Event.analysis_enabled.is_(True),
            EventSegment.analysis_enabled.is_(True),
            EventSegment.topic_weight > 0,
        )
    )
    documents: dict[str, _Document] = {}
    for event_id, context_id, timestamp, text, weight in rows:
        terms = extract_candidate_terms(text, include_trigrams=include_trigrams)
        weighted = Counter({term: count * float(weight) for term, count in terms.items()})
        previous = documents.get(event_id)
        documents[event_id] = _Document(
            event_id=event_id,
            context_id=context_id or event_id,
            timestamp=timestamp,
            terms=(previous.terms + weighted) if previous else weighted,
            acronyms=(previous.acronyms | extract_acronyms(text))
            if previous
            else extract_acronyms(text),
        )
    return list(documents.values())


def _candidate_metrics(
    selected_terms: set[str],
    *,
    document_frequency: Counter[str],
    term_contexts: dict[str, set[str]],
    total_term_score: Counter[str],
    document_count: int,
    min_document_frequency: int,
    manual_aliases: set[str],
    acronym_document_frequency: Counter[str],
) -> dict[str, _CandidateMetrics]:
    result = {}
    for term in sorted(selected_terms):
        frequency = document_frequency[term]
        tfidf_score = total_term_score[term] / max(1, frequency)
        is_acronym = (
            2 <= len(term) <= 6
            and frequency >= min_document_frequency
            and (
                document_count < 50
                or (frequency / max(1, document_count) <= 0.05 and tfidf_score >= 3.5)
            )
            and acronym_document_frequency[term] >= min_document_frequency
        )
        quality = assess_term(
            term,
            document_frequency=frequency,
            document_count=document_count,
            tfidf_score=tfidf_score,
            min_document_frequency=min_document_frequency,
            allow_short_acronym=is_acronym,
        )
        if term in manual_aliases:
            quality = TermQuality(
                normalized_term=quality.normalized_term,
                ngram_size=max(1, quality.ngram_size),
                language=quality.language,
                score=max(quality.score, 1.0),
                status="accepted",
                rejection_reason=None,
            )
        result[term] = _CandidateMetrics(
            term=term,
            document_frequency=frequency,
            context_count=len(term_contexts[term]),
            tfidf_score=tfidf_score,
            quality=quality,
            is_acronym=is_acronym,
            is_manual=term in manual_aliases,
        )
    return result


def _persist_candidates(
    session: Session, metrics: dict[str, _CandidateMetrics], *, analysis_run_id: str
) -> None:
    rows = [
        {
            "term_id": stable_term_id(term),
            "analysis_run_id": analysis_run_id,
            "term": metric.term,
            "normalized_term": metric.quality.normalized_term,
            "ngram_size": metric.quality.ngram_size,
            "language": metric.quality.language,
            "message_count": metric.document_frequency,
            "context_count": metric.context_count,
            "document_frequency": metric.document_frequency,
            "tfidf_score": metric.tfidf_score,
            "quality_score": metric.quality.score,
            "quality_status": metric.quality.status,
            "rejection_reason": metric.quality.rejection_reason,
            "is_active": metric.quality.status == "accepted",
        }
        for term, metric in metrics.items()
    ]
    if not rows:
        return
    session.execute(insert(CandidateTerm), rows)


def _candidate_assignments(
    documents: list[_Document],
    metrics: dict[str, _CandidateMetrics],
    *,
    candidate_terms_per_event: int,
) -> list[dict[str, object]]:
    rows = []
    for document in documents:
        scored = sorted(
            (
                (
                    frequency
                    * _inverse_document_frequency(len(documents), metric.document_frequency),
                    term,
                )
                for term, frequency in document.terms.items()
                if (metric := metrics.get(term)) is not None
            ),
            key=lambda item: (-item[0], -item[1].count(" "), item[1]),
        )
        ranked = scored[:candidate_terms_per_event]
        selected = {term for _score, term in ranked}
        ranked.extend(
            (score, term)
            for score, term in scored[candidate_terms_per_event:]
            if term not in selected and (metrics[term].is_acronym or metrics[term].is_manual)
        )
        rows.extend(
            {"event_id": document.event_id, "term_id": stable_term_id(term), "weight": score}
            for score, term in ranked
        )
    return rows


def _build_topic_definitions(
    metrics: dict[str, _CandidateMetrics],
    *,
    overrides: tuple[TopicOverride, ...],
    max_topics: int,
    document_count: int,
) -> tuple[_TopicDefinition, ...]:
    definitions: list[_TopicDefinition] = []
    manually_mapped: set[str] = set()
    for override in overrides:
        manually_mapped.update(override.aliases)
        definitions.append(
            _TopicDefinition(
                topic_id=stable_topic_id(override.name, manual_id=override.override_id),
                name=override.name,
                category=override.category,
                origin="manual",
                creation_method="override",
                primary_term=override.aliases[0],
                aliases=override.aliases,
            )
        )

    accepted = {
        term: metric for term, metric in metrics.items() if metric.quality.status == "accepted"
    }
    suppressed: set[str] = set()
    phrase_aliases: dict[str, set[str]] = defaultdict(set)
    phrases = sorted(
        (item for item in accepted.items() if item[1].quality.ngram_size >= 2),
        key=lambda item: (-item[1].quality.score, item[0]),
    )
    for phrase, phrase_metric in phrases:
        for token in term_tokens(phrase):
            unigram_metric = accepted.get(token)
            if unigram_metric and phrase_suppresses_unigram(
                phrase_metric.quality,
                unigram_metric.quality,
                phrase_document_frequency=phrase_metric.document_frequency,
                unigram_document_frequency=unigram_metric.document_frequency,
            ):
                suppressed.add(token)
                if (
                    phrase_metric.document_frequency / max(1, unigram_metric.document_frequency)
                    >= 0.6
                ):
                    phrase_aliases[phrase].add(token)

    groups: dict[str, list[str]] = defaultdict(list)
    for term, metric in accepted.items():
        if term in manually_mapped or term in suppressed:
            continue
        groups[_canonical_variant(term)].append(term)
    ranked_groups = sorted(
        groups.items(),
        key=lambda item: (
            -max(accepted[term].quality.score for term in item[1]),
            -max(accepted[term].quality.ngram_size for term in item[1]),
            item[0],
        ),
    )
    for canonical, terms in ranked_groups:
        ranked = sorted(
            terms,
            key=lambda term: (
                -accepted[term].quality.score,
                -accepted[term].quality.ngram_size,
                term,
            ),
        )
        primary = ranked[0]
        aliases = tuple(dict.fromkeys([*ranked, *sorted(phrase_aliases.get(primary, set()))]))
        primary_metric = accepted[primary]
        is_phrase = (
            document_count < 50
            and primary_metric.quality.ngram_size >= 2
            and not any(token in ALL_STOPWORDS for token in term_tokens(primary))
            and primary_metric.document_frequency >= (1 if document_count < 10 else 8)
            and primary_metric.context_count >= (1 if document_count < 10 else 8)
        )
        promotes_acronym = document_count < 50 and primary_metric.is_acronym
        qualifies = promotes_acronym or is_phrase
        if not qualifies:
            continue
        creation_method = (
            "alias_group" if promotes_acronym or len(terms) >= 2 else "high_confidence_phrase"
        )
        definitions.append(
            _TopicDefinition(
                topic_id=stable_topic_id(canonical),
                name=primary,
                category="topic",
                origin="automatic",
                creation_method=creation_method,
                primary_term=primary,
                aliases=aliases,
            )
        )
        if len(definitions) >= max_topics:
            break
    return tuple(definitions)


def _persist_topic_layer(
    session: Session,
    *,
    documents: list[_Document],
    metrics: dict[str, _CandidateMetrics],
    definitions: tuple[_TopicDefinition, ...],
    topics_per_event: int,
    analysis_run_id: str,
) -> tuple[int, int, int, int]:
    term_to_topics: dict[str, list[str]] = defaultdict(list)
    topic_terms = []
    for definition in definitions:
        for alias in definition.aliases:
            if alias not in metrics:
                continue
            term_to_topics[alias].append(definition.topic_id)
            topic_terms.append(
                {
                    "topic_id": definition.topic_id,
                    "term_id": stable_term_id(alias),
                    "analysis_run_id": analysis_run_id,
                    "relation_type": (
                        "manual"
                        if definition.origin == "manual"
                        else "primary"
                        if alias == definition.primary_term
                        else "alias"
                    ),
                }
            )

    topic_events: Counter[str] = Counter()
    topic_contexts: dict[str, set[str]] = defaultdict(set)
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    event_topic_rows = []
    document_topics: list[tuple[_Document, tuple[str, ...]]] = []
    for document in documents:
        scores: dict[str, float] = {}
        for term, frequency in document.terms.items():
            metric = metrics.get(term)
            if metric is None:
                continue
            score = frequency * _inverse_document_frequency(
                len(documents), metric.document_frequency
            )
            for topic_id in term_to_topics.get(term, []):
                scores[topic_id] = max(scores.get(topic_id, 0.0), score)
        chosen = tuple(
            topic_id
            for topic_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
                :topics_per_event
            ]
        )
        if not chosen:
            continue
        document_topics.append((document, chosen))
        for topic_id in chosen:
            event_topic_rows.append(
                {"event_id": document.event_id, "topic_id": topic_id, "weight": scores[topic_id]}
            )
            topic_events[topic_id] += 1
            topic_contexts[topic_id].add(document.context_id)
            if document.timestamp is not None:
                first_seen[topic_id] = min(
                    first_seen.get(topic_id, document.timestamp), document.timestamp
                )
                last_seen[topic_id] = max(
                    last_seen.get(topic_id, document.timestamp), document.timestamp
                )

    topic_rows = [
        {
            "topic_id": definition.topic_id,
            "analysis_run_id": analysis_run_id,
            "name": definition.name,
            "category": definition.category,
            "status": "active",
            "origin": definition.origin,
            "creation_method": definition.creation_method,
            "is_active": True,
            "message_count": topic_events[definition.topic_id],
            "conversation_count": len(topic_contexts[definition.topic_id]),
            "first_seen_at": first_seen.get(definition.topic_id),
            "last_seen_at": last_seen.get(definition.topic_id),
        }
        for definition in definitions
        if topic_events[definition.topic_id] or definition.origin == "manual"
    ]
    active_topic_ids = {row["topic_id"] for row in topic_rows}
    topic_terms = [row for row in topic_terms if row["topic_id"] in active_topic_ids]
    event_topic_rows = [row for row in event_topic_rows if row["topic_id"] in active_topic_ids]
    if topic_rows:
        session.execute(insert(Topic), topic_rows)
    if topic_terms:
        session.execute(insert(TopicTerm), topic_terms)
    if event_topic_rows:
        session.execute(
            insert(EventTopic),
            [{**row, "analysis_run_id": analysis_run_id} for row in event_topic_rows],
        )
    alias_rows = [
        {
            "topic_alias_id": _topic_alias_id(definition.topic_id, alias),
            "analysis_run_id": analysis_run_id,
            "topic_id": definition.topic_id,
            "normalized_alias": normalize_term(alias),
            "display_alias": alias,
            "alias_type": "manual" if definition.origin == "manual" else "term",
        }
        for definition in definitions
        if definition.topic_id in active_topic_ids
        for alias in definition.aliases
    ]
    if alias_rows:
        session.execute(insert(TopicAlias), alias_rows)

    message_pairs: Counter[tuple[str, str]] = Counter()
    pair_contexts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for document, assigned_topics in document_topics:
        visible = sorted(set(assigned_topics) & active_topic_ids)
        for pair in combinations(visible, 2):
            message_pairs[pair] += 1
            pair_contexts[pair].add(document.context_id)
    relation_rows = []
    topic_methods = {definition.topic_id: definition.creation_method for definition in definitions}
    for (source_id, target_id), message_count in sorted(message_pairs.items()):
        shared_context_count = len(pair_contexts[(source_id, target_id)])
        manually_approved = "override" in {
            topic_methods.get(source_id),
            topic_methods.get(target_id),
        }
        if shared_context_count < 2 and not manually_approved:
            continue
        denominator = math.sqrt(len(topic_contexts[source_id]) * len(topic_contexts[target_id]))
        relation_rows.append(
            {
                "relation_id": _relation_id(source_id, target_id),
                "analysis_run_id": analysis_run_id,
                "source_topic_id": source_id,
                "target_topic_id": target_id,
                "message_count": message_count,
                "conversation_count": shared_context_count,
                "weight": shared_context_count / denominator if denominator else 0.0,
            }
        )
    if relation_rows:
        session.execute(insert(TopicRelation), relation_rows)
    return len(topic_rows), len(topic_terms), len(event_topic_rows), len(relation_rows)


def _inverse_document_frequency(document_count: int, frequency: int) -> float:
    return math.log((document_count + 1) / (frequency + 1)) + 1.0


def _canonical_variant(term: str) -> str:
    tokens = list(term_tokens(term))
    canonical = [_simple_stem(token) for token in tokens]
    return " ".join(canonical)


def _simple_stem(token: str) -> str:
    if len(token) > 5 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 5 and token.endswith(("ers", "ens")):
        return token[:-1]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _relation_id(source_topic_id: str, target_topic_id: str) -> str:
    value = f"{source_topic_id}:{target_topic_id}"
    return f"topic-relation-{hashlib.sha256(value.encode()).hexdigest()}"


def _topic_alias_id(topic_id: str, alias: str) -> str:
    value = f"{topic_id}:{normalize_term(alias)}"
    return f"topic-alias-{hashlib.sha256(value.encode()).hexdigest()}"


def _clear_derived_analysis(session: Session) -> None:
    session.execute(delete(TopicRelation))
    session.execute(delete(EventTopic))
    session.execute(delete(TopicAlias))
    session.execute(delete(TopicTerm))
    session.execute(delete(EventCandidateTerm))
    session.execute(delete(Topic))
    session.execute(delete(CandidateTerm))
    session.execute(delete(EventSegment))
