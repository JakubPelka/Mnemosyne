import pytest
import sqlite3
import json
from unittest.mock import patch

from scripts.semantic_tagger.unit_serializer import compute_content_hash, serialize_semantic_unit
from scripts.semantic_tagger.cli import cmd_prepare_evaluation
import argparse


def test_hash_differentiation():
    """same canonical content gives different V1/V2/V3 hashes"""
    content = (
        "[CONTEXT_TITLE]Title[/CONTEXT_TITLE]\n\n[EVENT event_id=e1 role=user]\ntext\n[/EVENT]"
    )
    h1 = compute_content_hash("semantic-tags-v1", "unit-v2-whole-events", content)
    h2 = compute_content_hash("semantic-tags-v2", "unit-v2-whole-events", content)
    h3 = compute_content_hash("semantic-tags-v3", "unit-v2-whole-events", content)

    assert h1 != h2
    assert h2 != h3
    assert h1 != h3


@pytest.fixture
def fake_dbs(tmp_path):
    main_db = tmp_path / "main.sqlite3"
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, context_id TEXT, event_type TEXT, timestamp_start TEXT, title TEXT, text TEXT)"
        )
        conn.execute(
            "INSERT INTO events VALUES ('e1', 'ctx1', 'chatgpt_message', '2026-07-01', 'Title', 'Hello world')"
        )

    source_db = tmp_path / "source.sqlite3"
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            "CREATE TABLE tagging_run (run_id TEXT, schema_version TEXT, unit_strategy_version TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO tagging_run VALUES ('r_old', 'semantic-tags-v2', 'unit-v2-whole-events', '2026-07-01')"
        )

        # Calculate expected V2 hash
        manifest_data = {
            "title_included": True,
            "title_source": "ctx1",
            "segments": [{"event_id": "e1", "role": "user", "context_id": "ctx1"}],
        }
        content = serialize_semantic_unit(manifest_data, ["Hello world"], "Title")
        v2_hash = compute_content_hash("semantic-tags-v2", "unit-v2-whole-events", content)

        conn.execute("""
            CREATE TABLE tagging_unit (
                unit_id TEXT, context_id TEXT, segments_json TEXT, content_hash TEXT
            )
        """)
        conn.execute(
            "INSERT INTO tagging_unit VALUES (?, ?, ?, ?)",
            ("u_old", "ctx1", json.dumps(manifest_data), v2_hash),
        )

    return main_db, source_db, v2_hash, content


def test_prepare_evaluation_success(fake_dbs, tmp_path):
    """
    V2 source hash verifies with semantic-tags-v2
    V2 hash is never compared against V1 reconstruction
    target V3 hash differs from source V2 hash
    correct V2 source prepares one V3 job
    lineage fields are complete
    no hashlib monkeypatch is used
    """
    main_db, source_db, v2_hash, content = fake_dbs
    target_db = tmp_path / "target.sqlite3"
    manifest_path = tmp_path / "manifest.json"

    with open(manifest_path, "w") as f:
        json.dump([{"unit_id": "u_old", "context_id": "ctx1", "content_hash": v2_hash}], f)

    args = argparse.Namespace(
        source_sidecar=str(source_db),
        source_run_id=None,
        manifest=str(manifest_path),
        target_sidecar=str(target_db),
        model="qwen3:14b",
        target_schema_version="semantic-tags-v3",
        target_prompt_version="semantic-hybrid-v3",
        target_unit_strategy_version="unit-v3-whole-events",
        think=False,
        stream=False,
        temperature=0.0,
        seed=42,
        num_predict=2048,
        num_ctx=8192,
        expected_units=1,
        expected_contexts=1,
    )

    with (
        patch("scripts.semantic_tagger.cli.get_main_db") as mock_get_main,
        patch("scripts.semantic_tagger.content_loader.MAIN_DB_URI", f"file:{main_db}?mode=ro"),
    ):
        mock_get_main.return_value = sqlite3.connect(main_db)

        cmd_prepare_evaluation(args)

    assert target_db.exists(), "Target database should be created"

    with sqlite3.connect(target_db) as conn:
        conn.row_factory = sqlite3.Row
        jobs = conn.execute("SELECT * FROM tagging_job").fetchall()
        assert len(jobs) == 1, "Should prepare exactly one job"

        units = conn.execute("SELECT * FROM tagging_unit").fetchall()
        assert len(units) == 1, "Should create one unit"

        v3_hash = units[0]["content_hash"]
        assert v3_hash != v2_hash, "Target V3 hash must differ from source V2 hash"

        lineages = conn.execute("SELECT * FROM tagging_unit_lineage").fetchall()
        assert len(lineages) == 1, "Lineage fields must be complete"
        lin = lineages[0]
        assert lin["source_run_id"] == "r_old"
        assert lin["source_unit_id"] == "u_old"
        assert lin["source_content_hash"] == v2_hash
        assert lin["source_schema_version"] == "semantic-tags-v2"
        assert lin["source_unit_strategy_version"] == "unit-v2-whole-events"
        assert lin["target_unit_id"] == units[0]["unit_id"]
        assert lin["target_content_hash"] == v3_hash
        assert lin["target_schema_version"] == "semantic-tags-v3"
        assert lin["target_unit_strategy_version"] == "unit-v3-whole-events"
        assert lin["source_sidecar_sha256"] is not None
        assert len(lin["source_sidecar_sha256"]) == 64


def test_prepare_evaluation_mismatch_aborts(fake_dbs, tmp_path):
    """source mismatch aborts preparation"""
    main_db, source_db, v2_hash, content = fake_dbs
    target_db = tmp_path / "target.sqlite3"
    manifest_path = tmp_path / "manifest.json"

    with open(manifest_path, "w") as f:
        json.dump(
            [
                {
                    "unit_id": "u_old",
                    "context_id": "ctx1",
                    "content_hash": "invalid_hash_that_wont_match",
                }
            ],
            f,
        )

    args = argparse.Namespace(
        source_sidecar=str(source_db),
        source_run_id=None,
        manifest=str(manifest_path),
        target_sidecar=str(target_db),
        model="qwen3:14b",
        target_schema_version="semantic-tags-v3",
        target_prompt_version="semantic-hybrid-v3",
        target_unit_strategy_version="unit-v3-whole-events",
        think=False,
        stream=False,
        temperature=0.0,
        seed=42,
        num_predict=2048,
        num_ctx=8192,
        expected_units=1,
        expected_contexts=1,
    )

    with (
        patch("scripts.semantic_tagger.cli.get_main_db") as mock_get_main,
        patch("scripts.semantic_tagger.content_loader.MAIN_DB_URI", f"file:{main_db}?mode=ro"),
    ):
        mock_get_main.return_value = sqlite3.connect(main_db)

        cmd_prepare_evaluation(args)

    # Aborts before creating JobStore, so target sidecar won't exist
    assert not target_db.exists()
