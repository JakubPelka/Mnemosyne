from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.privacy import mask_private_text
from scripts.semantic_tagger.prompt_builder import build_tagger_prompt
from scripts.semantic_tagger.unit_serializer import serialize_semantic_unit


def test_mask_private_text():
    text = "Contact me at bob@example.com or visit https://secret.com/page. See /home/user/data/file.txt."
    masked = mask_private_text(text)
    assert "bob@example.com" not in masked
    assert "https://secret.com/page" not in masked
    assert "/home/user/data/file.txt" not in masked
    assert "[EMAIL]" in masked
    assert "[URL]" in masked
    assert "[PATH]" in masked


def test_unit_builder_deterministic_id():
    builder = UnitBuilder(max_events=2)
    events = [
        {"event_id": "e1", "text": "Hello", "timestamp_start": "2023-01-01", "event_type": "user"},
        {
            "event_id": "e2",
            "text": "Hi",
            "timestamp_start": "2023-01-02",
            "event_type": "assistant",
        },
    ]
    units1 = builder.build_units_for_context("ctx-1", events, "Title")
    units2 = builder.build_units_for_context("ctx-1", events, "Title")

    assert units1[0]["unit_id"] == units2[0]["unit_id"]
    assert units1[0]["content_hash"] == units2[0]["content_hash"]


def test_unit_builder_overlap():
    builder = UnitBuilder(max_events=2, overlap_events=1)
    events = [
        {"event_id": "e1", "text": "A"},
        {"event_id": "e2", "text": "B"},
        {"event_id": "e3", "text": "C"},
    ]
    units = builder.build_units_for_context("ctx-1", events)
    assert len(units) == 2
    assert units[0]["event_ids"] == ["e1", "e2"]
    assert units[1]["event_ids"] == ["e2", "e3"]


def test_explicit_chatgpt_roles_are_resolved_and_serialized():
    builder = UnitBuilder()
    events = [
        {
            "event_id": "e2",
            "text": "Synthetic assistant",
            "timestamp_start": "2026-01-02",
            "event_type": "chatgpt_message",
            "source_role": "assistant",
        },
        {
            "event_id": "e1",
            "text": "Synthetic user",
            "timestamp_start": "2026-01-01",
            "event_type": "chatgpt_message",
            "source_role": "user",
        },
    ]

    unit = builder.build_units_for_context("ctx", events)[0]
    roles = [segment["role"] for segment in unit["segments"]["segments"]]
    content = serialize_semantic_unit(unit["segments"], ["Synthetic user", "Synthetic assistant"])
    prompt = build_tagger_prompt(
        "semantic-hybrid-v3", False, False, False, content, unit["event_ids"]
    ).prompt

    assert unit["event_ids"] == ["e1", "e2"]
    assert roles == ["user", "assistant"]
    assert "role=user" in content
    assert "role=assistant" in content
    assert "role=unknown" not in content
    assert "role=user" in prompt
    assert "role=assistant" in prompt
    assert "role=unknown" not in prompt


def test_missing_or_unsupported_source_role_uses_existing_fallbacks():
    builder = UnitBuilder()
    events = [
        {"event_id": "e1", "text": "Synthetic", "event_type": "chatgpt_message"},
        {
            "event_id": "e2",
            "text": "Synthetic",
            "event_type": "human",
            "source_role": "unsupported",
        },
        {"event_id": "e3", "text": "Synthetic", "event_type": "bot"},
        {"event_id": "e4", "text": "Synthetic", "event_type": "tool"},
    ]

    unit = builder.build_units_for_context("ctx", events)[0]
    roles = [segment["role"] for segment in unit["segments"]["segments"]]

    assert roles == ["unknown", "user", "assistant", "tool"]


def test_role_aware_builds_are_deterministic_and_roles_affect_identity():
    builder = UnitBuilder()
    base = [
        {
            "event_id": "e1",
            "text": "Synthetic",
            "timestamp_start": "2026-01-01",
            "event_type": "chatgpt_message",
            "source_role": "user",
        },
        {
            "event_id": "e2",
            "text": "Synthetic",
            "timestamp_start": "2026-01-01",
            "event_type": "chatgpt_message",
            "source_role": "assistant",
        },
    ]

    first = builder.build_units_for_context("ctx", base)[0]
    repeated = builder.build_units_for_context("ctx", list(reversed(base)))[0]
    changed_roles = builder.build_units_for_context(
        "ctx", [dict(event, source_role="user") for event in base]
    )[0]

    assert first["event_ids"] == repeated["event_ids"] == changed_roles["event_ids"]
    assert first["unit_id"] == repeated["unit_id"]
    assert first["content_hash"] == repeated["content_hash"]
    assert first["segments"] == repeated["segments"]
    assert first["content_hash"] != changed_roles["content_hash"]
    assert first["unit_id"] != changed_roles["unit_id"]
