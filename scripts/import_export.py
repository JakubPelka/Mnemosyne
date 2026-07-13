#!/usr/bin/env python3
"""Import a local ChatGPT export into the private SQLite database."""

import argparse
import json
import os
from pathlib import Path

from backend.app.database import create_sqlite_engine, session_factory
from backend.app.services.import_chatgpt import import_chatgpt_export


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", type=Path, help="ZIP archive or unpacked export directory")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("MNEMOSYNE_DATABASE_PATH", "data/mnemosyne.sqlite3")),
    )
    args = parser.parse_args()

    engine = create_sqlite_engine(args.database)
    session_maker = session_factory(engine)
    with session_maker() as session:
        result = import_chatgpt_export(session, args.input_path)
    print(
        json.dumps(
            {
                "status": "completed",
                "import_run_id": result.import_run_id,
                "conversations": result.conversations,
                "events": result.events,
                "warnings": result.warnings,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
