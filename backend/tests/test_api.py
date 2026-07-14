from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.dependencies import get_session
from backend.app.database import create_sqlite_engine, session_factory, sqlite_url
from backend.app.main import app
from backend.app.models import (
    CandidateTerm,
    ChatGPTMessageModel,
    Event,
    EventTopic,
    Topic,
    TopicTerm,
)
from backend.app.services.import_chatgpt import import_chatgpt_export
from backend.app.services.topics import build_topics

FIXTURE_DIR = Path(__file__).parents[2] / "sample_data"


@dataclass(frozen=True)
class ApiFixture:
    client: TestClient
    make_session: sessionmaker[Session]
    garden_id: str
    topic_name: str
    target_event_id: str


@pytest.fixture
def api_fixture(tmp_path: Path) -> Iterator[ApiFixture]:
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
        selected_topic = session.scalar(
            select(Topic)
            .where(Topic.name.contains("garden"), Topic.is_active.is_(True))
            .order_by(Topic.name)
        )
        target_event_id = session.scalar(
            select(ChatGPTMessageModel.event_id).where(ChatGPTMessageModel.message_id == "pl-user")
        )
    assert selected_topic is not None
    assert target_event_id is not None

    def override_session() -> Iterator[Session]:
        with make_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield ApiFixture(
            client, make_session, selected_topic.topic_id, selected_topic.name, target_event_id
        )
    app.dependency_overrides.clear()


def test_response_models_are_exposed_in_openapi(api_fixture: ApiFixture) -> None:
    schema = api_fixture.client.get("/openapi.json").json()
    for path in (
        "/api/meta",
        "/api/graph",
        "/api/topics",
        "/api/topics/search",
        "/api/topics/{topic_id}",
        "/api/topics/{topic_id}/terms",
        "/api/topics/{topic_id}/occurrences",
        "/api/search/events",
        "/api/messages/{event_id}/context",
    ):
        response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert "$ref" in response_schema


def test_meta_returns_only_structural_metadata(api_fixture: ApiFixture) -> None:
    response = api_fixture.client.get("/api/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == "0.4.0"
    assert body["source_types"] == ["chatgpt"]
    assert body["topic_categories"] == ["topic"]
    assert body["counts"]["events"] == 11
    assert body["counts"]["topics"] > 0
    assert body["counts"]["relations"] == 0
    assert set(body) == {
        "earliest_event_at",
        "latest_event_at",
        "source_types",
        "topic_categories",
        "counts",
        "api_version",
    }


def test_graph_intensity_and_limited_context_api(api_fixture: ApiFixture) -> None:
    graph = api_fixture.client.get(
        "/api/graph",
        params={"min_occurrences": 1, "node_limit": 10, "source_type": "chatgpt"},
    )
    intensity = api_fixture.client.get(f"/api/topics/{api_fixture.garden_id}/intensity")
    context = api_fixture.client.get(
        f"/api/messages/{api_fixture.target_event_id}/context",
        params={"before": 1, "after": 1},
    )

    assert graph.status_code == 200
    assert graph.json()["nodes"]
    assert intensity.status_code == 200
    assert intensity.json()["months"][0]["month"] == "2024-03"
    assert context.status_code == 200
    assert len(context.json()["messages"]) == 2
    assert sum(message["is_target"] for message in context.json()["messages"]) == 1


def test_topic_search_and_detail(api_fixture: ApiFixture) -> None:
    search = api_fixture.client.get("/api/topics/search", params={"q": "garden", "limit": 5})
    detail = api_fixture.client.get(f"/api/topics/{api_fixture.garden_id}")
    missing = api_fixture.client.get("/api/topics/not-a-topic")

    assert search.status_code == 200
    assert any(item["topic_id"] == api_fixture.garden_id for item in search.json()["items"])
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == api_fixture.topic_name
    assert body["message_count"] == 1
    assert body["context_count"] == 1
    assert body["months"][0]["month"] == "2024-03"
    assert body["neighbors"] == []
    assert missing.status_code == 404
    assert missing.json()["detail"] == "topic_not_found"


def test_topic_list_terms_and_diagnostic_graph(api_fixture: ApiFixture) -> None:
    topics = api_fixture.client.get("/api/topics", params={"limit": 2, "offset": 0})
    terms = api_fixture.client.get(f"/api/topics/{api_fixture.garden_id}/terms")
    term_graph = api_fixture.client.get(
        "/api/graph",
        params={"view": "terms", "min_occurrences": 1, "node_limit": 20},
    )
    topic_graph = api_fixture.client.get(
        "/api/graph",
        params={"view": "topics", "min_occurrences": 1, "node_limit": 20},
    )

    assert topics.status_code == 200
    assert 0 < len(topics.json()["items"]) <= 2
    assert terms.status_code == 200
    assert terms.json()["items"]
    assert all(item["quality_status"] != "rejected" for item in terms.json()["items"])
    assert all(node["category"].startswith("candidate_") for node in term_graph.json()["nodes"])
    assert all(node["category"] == "topic" for node in topic_graph.json()["nodes"])


def test_term_layer_preserves_search_detail_occurrences_and_context(
    api_fixture: ApiFixture,
) -> None:
    search = api_fixture.client.get("/api/topics/search", params={"q": "GARDEN", "layer": "terms"})
    assert search.json()["items"]
    term_id = search.json()["items"][0]["topic_id"]
    detail = api_fixture.client.get(f"/api/topics/{term_id}", params={"layer": "terms"})
    occurrences = api_fixture.client.get(
        f"/api/topics/{term_id}/occurrences", params={"layer": "terms"}
    )

    assert all(item["layer"] == "terms" for item in search.json()["items"])
    assert detail.status_code == 200
    assert detail.json()["layer"] == "terms"
    assert detail.json()["months"]
    assert detail.json()["neighbors"]
    assert occurrences.status_code == 200
    assert occurrences.json()["items"]
    context = api_fixture.client.get(
        f"/api/messages/{occurrences.json()['items'][0]['event_id']}/context"
    )
    assert context.status_code == 200
    assert any(message["is_target"] for message in context.json()["messages"])


def test_rejected_topic_terms_require_diagnostic_opt_in(api_fixture: ApiFixture) -> None:
    with api_fixture.make_session() as session:
        rejected = session.scalar(
            select(CandidateTerm).where(CandidateTerm.quality_status == "rejected").limit(1)
        )
        assert rejected is not None
        session.add(
            TopicTerm(
                topic_id=api_fixture.garden_id,
                term_id=rejected.term_id,
                relation_type="alias",
            )
        )
        session.commit()

    default = api_fixture.client.get(f"/api/topics/{api_fixture.garden_id}/terms")
    diagnostic = api_fixture.client.get(
        f"/api/topics/{api_fixture.garden_id}/terms", params={"include_rejected": True}
    )

    assert all(item["quality_status"] != "rejected" for item in default.json()["items"])
    assert any(item["quality_status"] == "rejected" for item in diagnostic.json()["items"])


def test_occurrences_are_paginated_filtered_and_excerpted(api_fixture: ApiFixture) -> None:
    first_page = api_fixture.client.get(
        f"/api/topics/{api_fixture.garden_id}/occurrences",
        params={"limit": 1, "offset": 0},
    )
    after_range = api_fixture.client.get(
        f"/api/topics/{api_fixture.garden_id}/occurrences",
        params={"start": datetime(2025, 1, 1, tzinfo=UTC).isoformat()},
    )
    wrong_source = api_fixture.client.get(
        f"/api/topics/{api_fixture.garden_id}/occurrences",
        params={"source_type": "synthetic-other"},
    )

    assert first_page.status_code == 200
    body = first_page.json()
    assert body["total"] == 1
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    assert set(body["items"][0]) == {
        "event_id",
        "occurred_at",
        "role",
        "conversation_title",
        "snippet",
        "source_record_id",
        "match_type",
    }
    assert "messages" not in body["items"][0]
    assert "text" not in body["items"][0]
    assert after_range.json()["total"] == 0
    assert wrong_source.json()["total"] == 0


def test_fts_search_uses_pagination_time_source_and_privacy(api_fixture: ApiFixture) -> None:
    page = api_fixture.client.get(
        "/api/search/events",
        params={"q": "garden", "limit": 2, "offset": 0, "source_type": "chatgpt"},
    )
    no_time_match = api_fixture.client.get(
        "/api/search/events",
        params={"q": "garden", "start": "2025-01-01T00:00:00Z"},
    )
    no_source_match = api_fixture.client.get(
        "/api/search/events", params={"q": "garden", "source_type": "synthetic-other"}
    )

    assert page.status_code == 200
    assert page.json()["total"] >= 1
    assert len(page.json()["items"]) <= 2
    assert all("text" not in item and "messages" not in item for item in page.json()["items"])
    assert all(item["match_type"] == "prose" for item in page.json()["items"])
    assert no_time_match.json()["total"] == 0
    assert no_source_match.json()["total"] == 0

    with api_fixture.make_session() as session:
        session.execute(update(Event).values(privacy_level="sensitive"))
        session.commit()

    private_search = api_fixture.client.get("/api/search/events", params={"q": "garden"})
    sensitive_search = api_fixture.client.get(
        "/api/search/events", params={"q": "garden", "privacy_level": "sensitive"}
    )
    private_graph = api_fixture.client.get(
        "/api/graph", params={"min_occurrences": 1, "node_limit": 100}
    )
    sensitive_graph = api_fixture.client.get(
        "/api/graph",
        params={"min_occurrences": 1, "node_limit": 100, "privacy_level": "sensitive"},
    )

    assert private_search.json()["total"] == 0
    assert sensitive_search.json()["total"] >= 1
    assert private_graph.json()["nodes"] == []
    assert sensitive_graph.json()["nodes"]


def test_topic_occurrence_privacy_and_missing_topic(api_fixture: ApiFixture) -> None:
    with api_fixture.make_session() as session:
        event_id = session.scalar(
            select(EventTopic.event_id).where(EventTopic.topic_id == api_fixture.garden_id).limit(1)
        )
        assert event_id is not None
        session.execute(
            update(Event).where(Event.event_id == event_id).values(privacy_level="sensitive")
        )
        session.commit()

    private = api_fixture.client.get(
        f"/api/topics/{api_fixture.garden_id}/occurrences",
        params={"privacy_level": "private"},
    )
    sensitive = api_fixture.client.get(
        f"/api/topics/{api_fixture.garden_id}/occurrences",
        params={"privacy_level": "sensitive"},
    )
    missing = api_fixture.client.get("/api/topics/not-a-topic/occurrences")

    assert private.status_code == 200
    assert sensitive.status_code == 200
    assert private.json()["total"] + sensitive.json()["total"] == 1
    assert missing.status_code == 404
