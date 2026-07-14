from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from backend.app.database import create_sqlite_engine, session_factory, sqlite_url
from backend.app.models import CandidateTerm, Event, EventSegment, Source, Topic
from backend.app.services.catalog import search_events
from backend.app.services.segments import rebuild_event_segments, segment_text
from backend.app.services.topics import build_topics


def _database(tmp_path: Path):
    path = tmp_path / "segments.sqlite3"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sqlite_url(path))
    command.upgrade(config, "head")
    engine = create_sqlite_engine(path)
    return engine, session_factory(engine)


def _insert_event(make_session, value: str) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    with make_session() as session:
        session.add(
            Source(
                source_id="source-segments",
                source_type="chatgpt",
                name="Synthetic segments",
                imported_at=now,
                original_path_hash="synthetic",
                metadata_json={},
            )
        )
        session.flush()
        session.add(
            Event(
                event_id="event-segments",
                source_id="source-segments",
                source_record_id="record-segments",
                event_type="message",
                context_id="context-segments",
                timestamp_start=now,
                timestamp_end=None,
                title="Synthetic segments",
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


def test_segments_mixed_prose_code_and_prose_in_order() -> None:
    segments = segment_text(
        "Synthetic prose before.\n\n```python\ndef build_path(defaults=None):\n"
        "    return defaults\n```\n\nSynthetic prose after."
    )
    assert [segment.segment_type for segment in segments] == ["prose", "code", "prose"]
    assert segments[1].language == "python"
    assert segments[1].analysis_enabled is False
    assert segments[1].search_enabled is True


def test_resegmentation_is_idempotent_and_preserves_original_text(tmp_path: Path) -> None:
    _engine, make_session = _database(tmp_path)
    original = "Prose with `inline_value`.\n\n```bash\necho synthetic\n```"
    _insert_event(make_session, original)
    with make_session() as session:
        first = rebuild_event_segments(session)
        session.commit()
    with make_session() as session:
        first_ids = tuple(
            session.scalars(select(EventSegment.segment_id).order_by(EventSegment.segment_index))
        )
        second = rebuild_event_segments(session)
        session.commit()
    with make_session() as session:
        second_ids = tuple(
            session.scalars(select(EventSegment.segment_id).order_by(EventSegment.segment_index))
        )
        stored = session.scalar(select(Event.text).where(Event.event_id == "event-segments"))
    assert first == second == 4
    assert first_ids == second_ids
    assert stored == original


def test_code_and_logs_are_searchable_but_do_not_create_topics(tmp_path: Path) -> None:
    engine, make_session = _database(tmp_path)
    value = (
        "Analizujemy dane GIS w QGIS.\n\n"
        "```python\ndef build_path(defaults=None):\n    return defaults\n```\n\n"
        "Dalsza interpretacja danych przestrzennych.\n\n"
        "```text\nTraceback (most recent call last):\nSyntheticFailure marker\n```"
    )
    _insert_event(make_session, value)
    with make_session() as session:
        build_topics(session, min_document_frequency=1, max_topics=30)
    with make_session() as session:
        code = search_events(session, "build_path", content_scope="code")
        logs = search_events(session, "SyntheticFailure", content_scope="logs")
        prose = search_events(session, "GIS", content_scope="prose")
        code_topics = session.scalar(
            select(func.count())
            .select_from(Topic)
            .where(Topic.name.in_(("def", "return", "defaults", "none", "path")))
        )
        code_candidates = session.scalar(
            select(func.count())
            .select_from(CandidateTerm)
            .where(CandidateTerm.normalized_term.in_(("def", "return", "defaults", "none", "path")))
        )
        fts_rows = session.scalar(text("SELECT count(*) FROM event_segments_fts"))

    assert code.total == 1 and code.items[0].match_type == "code"
    assert logs.total == 1 and logs.items[0].match_type == "log"
    assert prose.total >= 1 and prose.items[0].match_type == "prose"
    assert code_topics == 0
    assert code_candidates == 0
    assert fts_rows == 4


def test_tool_artifacts_are_neither_analyzed_nor_searched() -> None:
    segments = segment_text("turn57file0 tool_call recipient=backend")
    assert len(segments) == 1
    assert segments[0].segment_type == "tool_artifact"
    assert segments[0].analysis_enabled is False
    assert segments[0].search_enabled is False
