from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_local_topic_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never let private local overrides influence synthetic tests."""

    monkeypatch.setenv("MNEMOSYNE_TOPIC_OVERRIDES", str(tmp_path / "no-local-overrides.yaml"))
