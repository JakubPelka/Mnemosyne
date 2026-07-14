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
    args = parser.parse_args()
    make_session = session_factory(create_sqlite_engine(args.database))
    with make_session() as session:
        payload = _summary(session) if args.summary else _term(session, args.term)
    print(json.dumps(payload, sort_keys=True))
    return 0


def _summary(session: object) -> dict[str, object]:
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
        "events": _count(session, Event),
        "segments": {str(key): value for key, value in segment_types.items()},
        "candidate_terms": {str(key): value for key, value in statuses.items()},
        "topics": _count(session, Topic),
        "aliases": _count(session, TopicAlias),
        "event_candidate_terms": _count(session, EventCandidateTerm),
        "event_topics": _count(session, EventTopic),
        "relations": _count(session, TopicRelation),
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
    }


def _term(session: object, value: str) -> dict[str, object]:
    normalized = normalize_term(value)
    candidate = session.scalar(
        select(CandidateTerm).where(CandidateTerm.normalized_term == normalized)
    )
    topic_count = int(
        session.scalar(
            select(func.count()).select_from(Topic).where(func.lower(Topic.name) == normalized)
        )
        or 0
    )
    alias_count = int(
        session.scalar(
            select(func.count())
            .select_from(TopicAlias)
            .where(TopicAlias.normalized_alias == normalized)
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
                    "SELECT count(*) FROM event_segments_fts WHERE event_segments_fts MATCH :query"
                ),
                {"query": f'"{normalized}"'},
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
