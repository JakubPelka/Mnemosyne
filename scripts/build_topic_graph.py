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
    parser.add_argument("--max-topics", type=int, default=500)
    parser.add_argument("--topics-per-event", type=int, default=5)
    args = parser.parse_args()

    engine = create_sqlite_engine(args.database)
    make_session = session_factory(engine)
    with make_session() as session:
        result = build_topics(
            session,
            min_document_frequency=args.min_frequency,
            max_topics=args.max_topics,
            topics_per_event=args.topics_per_event,
        )
    print(
        json.dumps(
            {
                "status": "completed",
                "documents": result.documents,
                "topics": result.topics,
                "assignments": result.assignments,
                "relations": result.relations,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
