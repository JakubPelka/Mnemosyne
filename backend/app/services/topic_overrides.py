from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from backend.app.nlp.quality import normalize_term


@dataclass(frozen=True, slots=True)
class TopicOverride:
    override_id: str
    name: str
    aliases: tuple[str, ...]
    category: str


def load_topic_overrides(path: Path | None = None) -> tuple[TopicOverride, ...]:
    configured = path or Path(
        os.getenv("MNEMOSYNE_TOPIC_OVERRIDES", "data/local_topic_overrides.yaml")
    )
    if not configured.exists():
        return ()
    payload = yaml.safe_load(configured.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("topics", []), list):
        raise ValueError("topic_overrides_invalid_structure")

    overrides = []
    seen_ids: set[str] = set()
    for item in payload.get("topics", []):
        if not isinstance(item, dict):
            raise ValueError("topic_override_invalid_entry")
        override_id = normalize_term(str(item.get("id", ""))).replace(" ", "_")
        name = " ".join(str(item.get("name", "")).split())
        category = normalize_term(str(item.get("category", "manual"))) or "manual"
        aliases_value = item.get("aliases", [])
        if (
            not override_id
            or not name
            or override_id in seen_ids
            or not isinstance(aliases_value, list)
        ):
            raise ValueError("topic_override_invalid_entry")
        aliases = tuple(
            dict.fromkeys(
                normalized for alias in aliases_value if (normalized := normalize_term(str(alias)))
            )
        )
        if not aliases:
            raise ValueError("topic_override_requires_alias")
        seen_ids.add(override_id)
        overrides.append(TopicOverride(override_id, name, aliases, category))
    return tuple(overrides)
