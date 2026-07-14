import json
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from backend.app.database import create_sqlite_engine, session_factory, sqlite_url
from backend.app.models import (
    AnalysisRun,
    CandidateTerm,
    ChatGPTConversationModel,
    ChatGPTMessageModel,
    Event,
    EventCandidateTerm,
    ImportRun,
    Source,
    Topic,
    TopicAlias,
    TopicRelation,
    EventTopic,
    TopicTerm,
)
from backend.app.services.context import get_message_context
from backend.app.services.graph import get_topic_graph
from backend.app.services.import_chatgpt import import_chatgpt_export
from backend.app.services.catalog import resolve_search_query, search_catalog
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


def _insert_analysis_events(make_session, texts: list[str]) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    with make_session() as session:
        session.add(
            Source(
                source_id="source-quality-regression",
                source_type="chatgpt",
                name="Synthetic quality fixture",
                imported_at=now,
                original_path_hash="synthetic-hash",
                metadata_json={},
            )
        )
        session.flush()
        for index, value in enumerate(texts):
            session.add(
                Event(
                    event_id=f"event-quality-{index}",
                    source_id="source-quality-regression",
                    source_record_id=f"record-quality-{index}",
                    event_type="message",
                    context_id=f"context-quality-{index}",
                    timestamp_start=now,
                    timestamp_end=None,
                    title=None,
                    text=value,
                    url=None,
                    location_id=None,
                    privacy_level="private",
                    is_active=True,
                    analysis_enabled=True,
                    raw_payload_reference=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()


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
        "candidate_terms",
        "event_candidate_terms",
        "topic_terms",
        "event_segments",
        "event_segments_fts",
        "analysis_runs",
        "topic_aliases",
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


def test_candidate_term_migration_preserves_existing_topic_assignments(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-topics.sqlite3"
    config = _alembic_config(database_path)
    command.upgrade(config, "f9a405ee6284")
    engine = create_sqlite_engine(database_path)
    now = "2024-01-01 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sources (source_id, source_type, name, imported_at, "
                "original_path_hash, metadata) VALUES "
                "('source-synthetic', 'chatgpt', 'Synthetic', :now, 'hash', '{}')"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO events (event_id, source_id, source_record_id, event_type, "
                "context_id, privacy_level, is_active, analysis_enabled, created_at, updated_at) "
                "VALUES ('event-synthetic', 'source-synthetic', 'record-synthetic', "
                "'message', 'context-synthetic', 'private', 1, 1, :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO topics (topic_id, name, category, message_count, "
                "conversation_count) VALUES "
                "('topic-synthetic', 'synthetic phrase', 'keyword', 1, 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO event_topics (event_id, topic_id, weight) "
                "VALUES ('event-synthetic', 'topic-synthetic', 2.0)"
            )
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM candidate_terms")) == 1
        assert connection.scalar(text("SELECT count(*) FROM topic_terms")) == 1
        assert connection.scalar(text("SELECT count(*) FROM event_candidate_terms")) == 1
        assert (
            connection.scalar(
                text("SELECT count(*) FROM topics WHERE origin = 'legacy' AND is_active = 1")
            )
            == 1
        )


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
    assert result.candidate_terms > result.topics
    assert result.accepted_terms > 0
    assert result.rejected_terms > 0
    assert result.topics > 0
    assert result.assignments > 0
    assert result.relations == 0

    with make_session() as session:
        garden = session.scalar(
            select(Topic)
            .where(Topic.name.contains("garden"), Topic.is_active.is_(True))
            .order_by(Topic.name)
        )
        assert garden is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(Topic)
                .where(Topic.name == "garden", Topic.is_active.is_(True))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CandidateTerm)
                .where(CandidateTerm.normalized_term == "garden")
            )
            == 1
        )
        intensity = topic_monthly_intensity(session, garden.topic_id)
        assert [(item.month, item.message_count) for item in intensity] == [("2024-03", 1)]
        excluded_assignments = session.scalar(
            select(func.count())
            .select_from(EventTopic)
            .join(Event, Event.event_id == EventTopic.event_id)
            .where(Event.analysis_enabled.is_(False))
        )
        assert excluded_assignments == 0
        assert session.scalar(select(func.count()).select_from(CandidateTerm)) > result.topics
        assert (
            session.scalar(
                select(func.count())
                .select_from(CandidateTerm)
                .where(CandidateTerm.quality_status == "rejected")
            )
            == result.rejected_terms
        )
        assert session.scalar(select(func.count()).select_from(TopicTerm)) == result.topic_terms
        assert session.scalar(select(func.count()).select_from(EventCandidateTerm)) > 0

    with make_session() as session:
        second = build_topics(session, min_document_frequency=1, max_topics=100, topics_per_event=5)
    assert second == result
    with make_session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AnalysisRun)
                .where(AnalysisRun.is_active.is_(True), AnalysisRun.status == "completed")
            )
            == 1
        )
        active_run_id = session.scalar(
            select(AnalysisRun.analysis_run_id).where(AnalysisRun.is_active.is_(True))
        )
        assert active_run_id is not None
        assert set(session.scalars(select(Topic.analysis_run_id))) == {active_run_id}


def test_applies_manual_topic_override_and_aliases(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    override_path = tmp_path / "topic-overrides.yaml"
    override_path.write_text(
        """
topics:
  - id: synthetic_garden_concept
    name: Synthetic Garden Concept
    aliases:
      - synthetic garden
      - garden
    category: synthetic
""".strip(),
        encoding="utf-8",
    )
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    with make_session() as session:
        import_chatgpt_export(session, FIXTURE_DIR)
    with make_session() as session:
        build_topics(
            session,
            min_document_frequency=1,
            max_topics=100,
            topics_per_event=5,
            overrides_path=override_path,
        )

    with make_session() as session:
        manual = session.scalar(select(Topic).where(Topic.name == "Synthetic Garden Concept"))
        assert manual is not None
        assert manual.origin == "manual"
        assert manual.creation_method == "override"
        assert manual.category == "synthetic"
        assert (
            session.scalar(
                select(func.count())
                .select_from(TopicTerm)
                .where(TopicTerm.topic_id == manual.topic_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(TopicAlias)
                .where(TopicAlias.topic_id == manual.topic_id)
            )
            == 2
        )


def test_acronym_search_grouping_and_no_singleton_topic_fill(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    _insert_analysis_events(
        make_session,
        [
            "GIS QGIS geodata spatial data home assistant return def none path defaults",
            "GIS QGIS geodata spatial data home assistant return def none path defaults",
            "GIS QGIS geodata spatial data home assistant return def none path defaults",
        ],
    )
    override_path = tmp_path / "topic-overrides.yaml"
    override_path.write_text(
        """
topics:
  - id: example_geodata
    name: Example geodata topic
    aliases: [gis, qgis, geodata, spatial data]
    category: example
""".strip(),
        encoding="utf-8",
    )
    with make_session() as session:
        result = build_topics(
            session,
            min_document_frequency=2,
            max_topics=30,
            overrides_path=override_path,
        )
    with make_session() as session:
        lower = search_catalog(session, "gis", layer="all")
        upper = search_catalog(session, "GIS", layer="all")
        mixed = resolve_search_query(session, "Gis")
        automatic_topics = session.scalars(
            select(Topic).where(Topic.is_active.is_(True), Topic.origin == "automatic")
        ).all()
        code_topics = session.scalar(
            select(func.count())
            .select_from(Topic)
            .where(
                Topic.is_active.is_(True),
                Topic.name.in_(("return", "def", "none", "path", "defaults")),
            )
        )
        rejected_code = session.scalar(
            select(func.count())
            .select_from(CandidateTerm)
            .where(CandidateTerm.rejection_reason == "code_token")
        )
        rejected_code_unigrams = session.scalar(
            select(func.count())
            .select_from(CandidateTerm)
            .where(
                CandidateTerm.rejection_reason == "code_token",
                CandidateTerm.normalized_term.in_(("return", "def", "none", "path", "defaults")),
            )
        )

    assert lower and upper
    assert mixed is not None
    assert mixed.match_kind in {"exact_topic", "exact_alias"}
    assert {item.name for item in lower} == {item.name for item in upper}
    assert any(item.layer == "terms" for item in lower)
    assert any(item.layer == "topics" for item in lower)
    assert result.topics < 30
    assert code_topics == 0
    assert rejected_code >= 5
    assert rejected_code_unigrams == 5
    assert all(topic.name in {"gis", "qgis"} or " " in topic.name for topic in automatic_topics)


def test_topic_graph_does_not_fill_node_limit_with_singletons(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    _insert_analysis_events(
        make_session,
        [
            "syntheticone",
            "syntheticone",
            "synthetictwo",
            "synthetictwo",
            "syntheticthree",
            "syntheticthree",
        ],
    )
    override_path = tmp_path / "three-topics.yaml"
    override_path.write_text(
        """
topics:
  - id: example_one
    name: Example One
    aliases: [syntheticone]
    category: example
  - id: example_two
    name: Example Two
    aliases: [synthetictwo]
    category: example
  - id: example_three
    name: Example Three
    aliases: [syntheticthree]
    category: example
""".strip(),
        encoding="utf-8",
    )
    with make_session() as session:
        result = build_topics(
            session,
            min_document_frequency=2,
            max_topics=30,
            overrides_path=override_path,
        )
    with make_session() as session:
        graph = get_topic_graph(session, min_occurrences=1, node_limit=30)

    assert result.topics == 3
    assert len(graph.nodes) == 3


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
            categories=frozenset({"topic"}),
            min_occurrences=1,
            min_edge_messages=1,
            node_limit=3,
        )

    assert len(graph.nodes) == 1
    assert all(node.first_seen_at.month == 3 for node in graph.nodes if node.first_seen_at)
    assert all(edge.message_count >= 1 for edge in graph.edges)

    selected = graph.nodes[0].topic_id
    with make_session() as session:
        neighbors = get_topic_graph(
            session,
            min_occurrences=1,
            node_limit=100,
            selected_topic_id=selected,
            neighbors_only=True,
        )
    assert selected in {node.topic_id for node in neighbors.nodes}
    assert neighbors.edges == ()


def test_topic_relations_require_independent_contexts(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path)
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    _insert_analysis_events(
        make_session,
        [
            "Synthetic maps spatial portal",
            "Synthetic maps spatial portal",
            "Synthetic maps spatial portal",
        ],
    )
    override_path = tmp_path / "relations.yaml"
    override_path.write_text(
        """
topics:
  - id: example_maps
    name: Example Maps
    aliases: [synthetic maps]
    category: example
  - id: example_portal
    name: Example Portal
    aliases: [spatial portal]
    category: example
""".strip(),
        encoding="utf-8",
    )
    with make_session() as session:
        result = build_topics(
            session,
            min_document_frequency=1,
            overrides_path=override_path,
        )
    with make_session() as session:
        maps_id = session.scalar(select(Topic.topic_id).where(Topic.name == "Example Maps"))
        portal_id = session.scalar(select(Topic.topic_id).where(Topic.name == "Example Portal"))
        relation = session.scalar(
            select(TopicRelation).where(
                TopicRelation.source_topic_id.in_((maps_id, portal_id)),
                TopicRelation.target_topic_id.in_((maps_id, portal_id)),
            )
        )
    assert result.relations >= 1
    assert relation is not None
    assert relation.conversation_count == 3
    assert relation.weight == 1.0


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
