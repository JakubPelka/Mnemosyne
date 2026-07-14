#!/usr/bin/env python3
"""Read-only, privacy-safe diagnostics for the local derived analysis."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import func, select, text

from backend.app.database import create_sqlite_engine, session_factory
from backend.app.models import (
    AnalysisRun,
    CandidateTerm,
    CandidateTermRelation,
    Event,
    EventCandidateTerm,
    EventSegment,
    EventTopic,
    Topic,
    TopicAlias,
    TopicRelation,
    TopicTerm,
)
from backend.app.nlp.quality import normalize_term
from backend.app.services.catalog import resolve_search_query
from backend.app.services.analysis_runs import active_analysis_run_id
from backend.app.services.graph import get_candidate_term_graph, get_topic_graph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("MNEMOSYNE_DATABASE_PATH", "data/mnemosyne.sqlite3")),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--summary", action="store_true")
    mode.add_argument("--term")
    mode.add_argument("--graph", choices=("terms", "topics"))
    args = parser.parse_args()
    make_session = session_factory(create_sqlite_engine(args.database))
    with make_session() as session:
        if args.summary:
            payload = _summary(session)
        elif args.graph:
            payload = _graph(session, args.graph)
        else:
            payload = _term(session, args.term)
    print(json.dumps(payload, sort_keys=True))
    return 0


def _summary(session: object) -> dict[str, object]:
    active_id = active_analysis_run_id(session)
    segment_types = dict(
        session.execute(
            select(EventSegment.segment_type, func.count()).group_by(EventSegment.segment_type)
        ).all()
    )
    statuses = dict(
        session.execute(
            select(CandidateTerm.quality_status, func.count()).group_by(
                CandidateTerm.quality_status
            )
        ).all()
    )
    run_versions = dict(
        session.execute(
            select(AnalysisRun.analysis_version, func.count()).group_by(
                AnalysisRun.analysis_version
            )
        ).all()
    )
    return {
        "active_analysis_run_id": active_id,
        "events": _count(session, Event),
        "segments": {str(key): value for key, value in segment_types.items()},
        "candidate_terms": {str(key): value for key, value in statuses.items()},
        "topics": _count(session, Topic),
        "aliases": _count(session, TopicAlias),
        "event_candidate_terms": _count(session, EventCandidateTerm),
        "event_topics": _count(session, EventTopic),
        "topic_relations": _count(session, TopicRelation),
        "candidate_term_relations": _count(session, CandidateTermRelation),
        "analysis_runs_by_version": run_versions,
        "active_completed_runs": int(
            session.scalar(
                select(func.count())
                .select_from(AnalysisRun)
                .where(AnalysisRun.is_active, AnalysisRun.status == "completed")
            )
            or 0
        ),
        "derived_without_run": sum(
            int(
                session.scalar(
                    select(func.count()).select_from(model).where(model.analysis_run_id.is_(None))
                )
                or 0
            )
            for model in (
                EventSegment,
                CandidateTerm,
                CandidateTermRelation,
                EventCandidateTerm,
                Topic,
                TopicTerm,
                EventTopic,
                TopicRelation,
            )
        ),
        "orphan_topic_relations": int(
            session.scalar(
                text(
                    "SELECT count(*) FROM topic_relations r "
                    "LEFT JOIN topics s ON s.topic_id=r.source_topic_id "
                    "LEFT JOIN topics t ON t.topic_id=r.target_topic_id "
                    "WHERE s.topic_id IS NULL OR t.topic_id IS NULL"
                )
            )
            or 0
        ),
        "orphan_candidate_term_relations": int(
            session.scalar(
                text(
                    "SELECT count(*) FROM candidate_term_relations r "
                    "LEFT JOIN candidate_terms s ON s.term_id=r.source_term_id "
                    "LEFT JOIN candidate_terms t ON t.term_id=r.target_term_id "
                    "WHERE s.term_id IS NULL OR t.term_id IS NULL"
                )
            )
            or 0
        ),
        "inactive_run_records": _inactive_run_records(session),
    }


def _graph(session: object, layer: str) -> dict[str, object]:
    run_id = active_analysis_run_id(session)
    graph = (
        get_candidate_term_graph(session, node_limit=30, min_relation_weight=0.15)
        if layer == "terms"
        else get_topic_graph(session, node_limit=30, min_relation_weight=0.15)
    )
    active_candidates = int(
        session.scalar(
            select(func.count())
            .select_from(CandidateTerm)
            .where(
                CandidateTerm.analysis_run_id == run_id,
                CandidateTerm.is_active,
            )
        )
        or 0
    )
    return {
        "layer": layer,
        "active_analysis_run_id": run_id,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "isolated_nodes": len(graph.nodes)
        - len(
            {
                node_id
                for edge in graph.edges
                for node_id in (edge.source_topic_id, edge.target_topic_id)
            }
        ),
        "relation_source": ("candidate_term_relations" if layer == "terms" else "topic_relations"),
        "filters_active_run": True,
        "edge_filter_preserves_nodes": layer == "topics"
        or not active_candidates
        or bool(graph.nodes),
    }


def _inactive_run_records(session: object) -> int:
    active_id = active_analysis_run_id(session)
    if active_id is None:
        return 0
    return sum(
        int(
            session.scalar(
                select(func.count()).select_from(model).where(model.analysis_run_id != active_id)
            )
            or 0
        )
        for model in (
            EventSegment,
            CandidateTerm,
            CandidateTermRelation,
            EventCandidateTerm,
            Topic,
            TopicTerm,
            EventTopic,
            TopicRelation,
        )
    )


def _term(session: object, value: str) -> dict[str, object]:
    normalized = normalize_term(value)
    active_id = active_analysis_run_id(session)
    candidate = session.scalar(
        select(CandidateTerm).where(
            CandidateTerm.normalized_term == normalized,
            CandidateTerm.analysis_run_id == active_id,
        )
    )
    topic_count = int(
        session.scalar(
            select(func.count())
            .select_from(Topic)
            .where(
                func.lower(Topic.name) == normalized,
                Topic.analysis_run_id == active_id,
            )
        )
        or 0
    )
    alias_count = int(
        session.scalar(
            select(func.count())
            .select_from(TopicAlias)
            .join(Topic, Topic.topic_id == TopicAlias.topic_id)
            .where(
                TopicAlias.normalized_alias == normalized,
                Topic.analysis_run_id == active_id,
            )
        )
        or 0
    )
    event_pattern = f"%{normalized}%"
    resolution = resolve_search_query(session, value)
    return {
        "query": value,
        "event_text_matches": int(
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(func.lower(Event.text).like(event_pattern))
            )
            or 0
        ),
        "prose_segment_matches": int(
            session.scalar(
                select(func.count())
                .select_from(EventSegment)
                .where(
                    EventSegment.analysis_run_id == active_id,
                    EventSegment.segment_type == "prose",
                    func.lower(EventSegment.text).like(event_pattern),
                )
            )
            or 0
        ),
        "candidate_exists": candidate is not None,
        "candidate_active": bool(candidate and candidate.is_active),
        "candidate_status": candidate.quality_status if candidate else None,
        "rejection_reason": candidate.rejection_reason if candidate else None,
        "candidate_analysis_run_id": candidate.analysis_run_id if candidate else None,
        "exact_topic_count": topic_count,
        "exact_alias_count": alias_count,
        "segment_fts_matches": int(
            session.scalar(
                text(
                    "SELECT count(*) FROM event_segments_fts "
                    "JOIN event_segments s ON s.rowid=event_segments_fts.rowid "
                    "WHERE event_segments_fts MATCH :query AND s.analysis_run_id=:run_id"
                ),
                {"query": f'"{normalized}"', "run_id": active_id},
            )
            or 0
        ),
        "search_resolution": resolution.match_kind if resolution else None,
        "search_layer": resolution.item.layer if resolution else None,
        "analysis_versions": dict(
            session.execute(
                select(AnalysisRun.analysis_version, func.count()).group_by(
                    AnalysisRun.analysis_version
                )
            ).all()
        ),
    }


def _count(session: object, model: object) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
