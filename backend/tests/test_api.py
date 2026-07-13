from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.dependencies import get_session
from backend.app.database import create_sqlite_engine, session_factory, sqlite_url
from backend.app.main import app
from backend.app.models import ChatGPTMessageModel, Topic
from backend.app.services.import_chatgpt import import_chatgpt_export
from backend.app.services.topics import build_topics

FIXTURE_DIR = Path(__file__).parents[2] / "sample_data"


def test_graph_intensity_and_context_api(tmp_path: Path) -> None:
    database_path = tmp_path / "api.sqlite3"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))
    command.upgrade(config, "head")
    engine = create_sqlite_engine(database_path)
    make_session = session_factory(engine)
    with make_session() as session:
        import_chatgpt_export(session, FIXTURE_DIR)
    with make_session() as session:
        build_topics(session, min_document_frequency=1, max_topics=100, topics_per_event=5)
    with make_session() as session:
        garden_id = session.scalar(select(Topic.topic_id).where(Topic.name == "garden"))
        event_id = session.scalar(
            select(ChatGPTMessageModel.event_id).where(ChatGPTMessageModel.message_id == "pl-user")
        )
    assert garden_id is not None
    assert event_id is not None

    def override_session() -> Iterator[Session]:
        with make_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        graph = client.get(
            "/api/graph",
            params={"min_occurrences": 1, "node_limit": 10, "source_type": "chatgpt"},
        )
        intensity = client.get(f"/api/topics/{garden_id}/intensity")
        context = client.get(f"/api/messages/{event_id}/context", params={"before": 1, "after": 1})
    finally:
        app.dependency_overrides.clear()

    assert graph.status_code == 200
    assert graph.json()["nodes"]
    assert intensity.status_code == 200
    assert intensity.json()["months"] == [
        {"month": "2024-03", "message_count": 1, "weight": intensity.json()["months"][0]["weight"]}
    ]
    assert context.status_code == 200
    assert len(context.json()["messages"]) == 2
