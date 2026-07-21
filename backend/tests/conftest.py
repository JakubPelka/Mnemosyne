from pathlib import Path

import pytest

from scripts.semantic_tagger import vocabulary_store as vocabulary_store_module


@pytest.fixture(autouse=True)
def isolate_local_topic_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never let private local overrides influence synthetic tests."""

    monkeypatch.setenv("MNEMOSYNE_TOPIC_OVERRIDES", str(tmp_path / "no-local-overrides.yaml"))


@pytest.fixture(autouse=True)
def isolate_semantic_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Route production-default vocabulary construction to a per-test database."""
    test_db = tmp_path / "semantic_vocabulary.test.sqlite3"
    real_store = vocabulary_store_module.VocabularyStore

    def test_store(db_path: Path = test_db):
        return real_store(db_path=db_path)

    monkeypatch.setattr(vocabulary_store_module, "VocabularyStore", test_store)
    return test_db
