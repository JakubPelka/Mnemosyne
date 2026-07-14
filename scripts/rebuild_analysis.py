#!/usr/bin/env python3
"""Safely preview or rebuild all derived local analysis records."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import func, select

from backend.app.database import create_sqlite_engine, session_factory
from backend.app.models import AnalysisRun, CandidateTerm, Event, EventSegment, Topic, TopicRelation
from backend.app.services.topics import build_topics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("MNEMOSYNE_DATABASE_PATH", "data/mnemosyne.sqlite3")),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    make_session = session_factory(create_sqlite_engine(args.database))
    with make_session() as session:
        before = _counts(session)
        if args.dry_run:
            print(json.dumps({"status": "dry_run", "would_replace": before}, sort_keys=True))
            return 0
        result = build_topics(session)
        after = _counts(session)
    print(
        json.dumps(
            {
                "status": "completed",
                "before": before,
                "after": after,
                "documents": result.documents,
                "rejection_counts": result.rejection_counts,
            },
            sort_keys=True,
        )
    )
    return 0


def _counts(session: object) -> dict[str, int]:
    return {
        "events": _count(session, Event),
        "segments": _count(session, EventSegment),
        "candidate_terms": _count(session, CandidateTerm),
        "topics": _count(session, Topic),
        "relations": _count(session, TopicRelation),
        "analysis_runs": _count(session, AnalysisRun),
        "active_analysis_runs": int(
            session.scalar(
                select(func.count()).select_from(AnalysisRun).where(AnalysisRun.is_active)
            )
            or 0
        ),
    }


def _count(session: object, model: object) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
