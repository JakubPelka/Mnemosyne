from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from backend.app.database import create_sqlite_engine, session_factory, sqlite_url
from backend.app.models import (
    ChatGPTConversationModel,
    ChatGPTMessageModel,
    Event,
    ImportRun,
    Source,
)
from backend.app.services.import_chatgpt import import_chatgpt_export

FIXTURE_DIR = Path(__file__).parents[2] / "sample_data"


def _migrated_database(tmp_path: Path) -> Path:
    database_path = tmp_path / "mnemosyne.sqlite3"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))
    command.upgrade(config, "head")
    return database_path


def test_migration_creates_shared_schema_and_fts(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)

    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
            )
        }

    assert {
        "sources",
        "events",
        "entities",
        "topics",
        "event_topics",
        "event_entities",
        "event_relations",
        "import_runs",
        "chatgpt_conversations",
        "chatgpt_messages",
        "events_fts",
    } <= tables


def test_reimport_is_idempotent_and_searchable(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)

    with make_session() as session:
        first = import_chatgpt_export(session, FIXTURE_DIR)
    with make_session() as session:
        second = import_chatgpt_export(session, FIXTURE_DIR)

    assert first.conversations == second.conversations == 3
    assert first.events == second.events == 11

    with make_session() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        assert session.scalar(select(func.count()).select_from(Event)) == 11
        assert session.scalar(select(func.count()).select_from(ChatGPTConversationModel)) == 3
        assert session.scalar(select(func.count()).select_from(ChatGPTMessageModel)) == 8
        assert (
            session.scalar(
                select(func.count())
                .select_from(ChatGPTMessageModel)
                .where(ChatGPTMessageModel.message_id == "shared-source-message-id")
            )
            == 2
        )
        assert session.scalar(select(func.count()).select_from(ImportRun)) == 2
        assert session.scalar(
            text("SELECT count(*) FROM events_fts WHERE events_fts MATCH 'garden'")
        )
        assert session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.timestamp_start >= datetime(2024, 2, 1, tzinfo=UTC))
        )
