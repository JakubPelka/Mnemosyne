#!/usr/bin/env python3
"""Build the local keyword index and topic co-occurrence graph."""

import argparse
import json
import os
from pathlib import Path

from backend.app.database import create_sqlite_engine, session_factory
from backend.app.services.topics import build_topics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("MNEMOSYNE_DATABASE_PATH", "data/mnemosyne.sqlite3")),
    )
    parser.add_argument("--min-frequency", type=int, default=5)
    parser.add_argument("--max-candidate-terms", type=int, default=5000)
    parser.add_argument("--max-topics", type=int, default=200)
    parser.add_argument("--topics-per-event", type=int, default=5)
    args = parser.parse_args()

    engine = create_sqlite_engine(args.database)
    make_session = session_factory(engine)
    with make_session() as session:
        result = build_topics(
            session,
            min_document_frequency=args.min_frequency,
            max_candidate_terms=args.max_candidate_terms,
            max_topics=args.max_topics,
            topics_per_event=args.topics_per_event,
        )
    print(
        json.dumps(
            {
                "status": "completed",
                "documents": result.documents,
                "candidate_terms": result.candidate_terms,
                "accepted_terms": result.accepted_terms,
                "rejected_terms": result.rejected_terms,
                "topics": result.topics,
                "topic_terms": result.topic_terms,
                "assignments": result.assignments,
                "relations": result.relations,
                "rejection_counts": result.rejection_counts,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
