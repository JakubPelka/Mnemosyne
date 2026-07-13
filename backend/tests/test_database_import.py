import json
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
    Topic,
    EventTopic,
)
from backend.app.services.context import get_message_context
from backend.app.services.graph import get_topic_graph
from backend.app.services.import_chatgpt import import_chatgpt_export
from backend.app.services.topics import build_topics, topic_monthly_intensity

FIXTURE_DIR = Path(__file__).parents[2] / "sample_data"


def _migrated_database(tmp_path: Path) -> Path:
    database_path = tmp_path / "mnemosyne.sqlite3"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))
    command.upgrade(config, "head")
    return database_path


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))
    return config


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


def test_second_revision_upgrades_an_existing_database(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.sqlite3"
    config = _alembic_config(database_path)
    command.upgrade(config, "4d94150c947c")
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        before = {row[1] for row in connection.execute(text("PRAGMA table_info(events)"))}
    assert "is_active" not in before

    command.upgrade(config, "head")

    with engine.connect() as connection:
        after = {row[1] for row in connection.execute(text("PRAGMA table_info(events)"))}
        relation_table = connection.scalar(
            text("SELECT count(*) FROM sqlite_master WHERE name='topic_relations'")
        )
    assert {"is_active", "analysis_enabled", "context_id"} <= after
    assert relation_table == 1


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


def test_missing_records_are_marked_inactive_on_new_export(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    reduced_export = tmp_path / "reduced-export"
    reduced_export.mkdir()
    fixture = json.loads((FIXTURE_DIR / "conversations-000.json").read_text(encoding="utf-8"))
    (reduced_export / "conversations-000.json").write_text(
        json.dumps(fixture[:2]), encoding="utf-8"
    )

    with make_session() as session:
        import_chatgpt_export(session, FIXTURE_DIR)
    with make_session() as session:
        import_chatgpt_export(session, reduced_export)

    with make_session() as session:
        assert (
            session.scalar(select(func.count()).select_from(Event).where(Event.is_active.is_(True)))
            == 7
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.is_active.is_(False))
            )
            == 4
        )


def test_builds_local_topics_relations_and_monthly_intensity(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    with make_session() as session:
        import_chatgpt_export(session, FIXTURE_DIR)
    with make_session() as session:
        result = build_topics(session, min_document_frequency=1, max_topics=100, topics_per_event=5)

    assert result.documents == 5
    assert result.topics > 0
    assert result.assignments > 0
    assert result.relations > 0

    with make_session() as session:
        garden = session.scalar(select(Topic).where(Topic.name == "garden"))
        assert garden is not None
        intensity = topic_monthly_intensity(session, garden.topic_id)
        assert [(item.month, item.message_count) for item in intensity] == [("2024-03", 1)]
        excluded_assignments = session.scalar(
            select(func.count())
            .select_from(EventTopic)
            .join(Event, Event.event_id == EventTopic.event_id)
            .where(Event.analysis_enabled.is_(False))
        )
        assert excluded_assignments == 0


def test_filters_and_limits_topic_graph(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    with make_session() as session:
        import_chatgpt_export(session, FIXTURE_DIR)
    with make_session() as session:
        build_topics(session, min_document_frequency=1, max_topics=100, topics_per_event=5)
    with make_session() as session:
        graph = get_topic_graph(
            session,
            start=datetime(2024, 3, 1, tzinfo=UTC),
            end=datetime(2024, 4, 1, tzinfo=UTC),
            source_type="chatgpt",
            categories=frozenset({"keyword"}),
            min_occurrences=1,
            min_edge_messages=1,
            node_limit=3,
        )

    assert len(graph.nodes) == 3
    assert all(node.first_seen_at.month == 3 for node in graph.nodes if node.first_seen_at)
    assert all(edge.message_count >= 1 for edge in graph.edges)

    selected = next(node.topic_id for node in graph.nodes if node.name == "garden")
    with make_session() as session:
        neighbors = get_topic_graph(
            session,
            min_occurrences=1,
            node_limit=100,
            selected_topic_id=selected,
            neighbors_only=True,
        )
    assert selected in {node.topic_id for node in neighbors.nodes}
    assert any(selected in {edge.source_topic_id, edge.target_topic_id} for edge in neighbors.edges)


def test_loads_limited_context_on_the_active_branch(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    with make_session() as session:
        import_chatgpt_export(session, FIXTURE_DIR)
    with make_session() as session:
        target_event_id = session.scalar(
            select(ChatGPTMessageModel.event_id).where(ChatGPTMessageModel.message_id == "pl-user")
        )
        assert target_event_id is not None
        context = get_message_context(session, target_event_id, before=2, after=1)

    assert context is not None
    assert [message.message_id for message in context.messages] == [
        "pl-user",
        "pl-assistant-current",
    ]
    assert [message.is_target for message in context.messages] == [True, False]
