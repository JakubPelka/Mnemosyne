import os
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.app.database import create_sqlite_engine, session_factory


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    path = Path(os.getenv("MNEMOSYNE_DATABASE_PATH", "data/mnemosyne.sqlite3"))
    return create_sqlite_engine(path)


def get_session() -> Iterator[Session]:
    make_session = session_factory(get_engine())
    with make_session() as session:
        yield session
