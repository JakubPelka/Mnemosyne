from types import SimpleNamespace

import pytest

from scripts.semantic_tagger.prompt_budget import (
    PROMPT_ESTIMATOR_VERSION,
    PromptBudgetConfig,
    PromptBudgetError,
    estimate_prompt_tokens,
)
from scripts.semantic_tagger.prompt_builder import (
    SUPPORTED_PROMPT_VARIANTS,
    _rendered_prompt_template,
    build_final_tagger_prompt,
    build_supported_final_prompt_variants,
)
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.unit_planner import (
    PromptBudgetUnitPlanner,
    UnitPlanningError,
)
from scripts.semantic_tagger.unit_serializer import serialize_semantic_unit
from scripts.semantic_tagger.worker import build_worker_final_prompt


def _event(
    event_id: str,
    text: str,
    timestamp: str,
    role: str = "user",
) -> dict:
    return {
        "event_id": event_id,
        "context_id": "ctx",
        "text": text,
        "timestamp_start": timestamp,
        "event_type": "chatgpt_message",
        "source_role": role,
    }


def _planner(
    *,
    max_prompt_tokens: int = 5632,
    max_events: int = 10,
    overlap_events: int = 1,
    backtrack: int = 256,
) -> PromptBudgetUnitPlanner:
    budget = PromptBudgetConfig(
        num_ctx=8192,
        max_prompt_tokens=max_prompt_tokens,
        num_predict=0,
        safety_margin=0,
        chunk_overlap_characters=256,
        chunk_boundary_backtrack_characters=backtrack,
    )
    return PromptBudgetUnitPlanner(
        prompt_version="semantic-hybrid-v3",
        schema_version="semantic-tags-v3",
        budget=budget,
        max_events=max_events,
        overlap_events=overlap_events,
    )


def _segments(units: list[dict]) -> list[dict]:
    return [segment for unit in units for segment in unit["segments"]["segments"]]


def test_estimator_exact_integer_formula_and_multibyte_utf8():
    for prompt in ("", "abc", "zażółć 🐦 漢字"):
        expected = (13 * len(prompt.encode("utf-8")) + 39) // 40
        assert estimate_prompt_tokens(prompt) == expected


@pytest.mark.parametrize(
    ("source_text", "expected_signal"),
    [
        ("plain synthetic text", None),
        ("```python\ndef synthetic():\n    pass\n```", "contains_code"),
        ("ERROR synthetic failure", "contains_logs"),
        ("https://example.invalid/synthetic", "contains_urls"),
    ],
)
def test_planner_and_worker_share_byte_identical_final_prompt(source_text, expected_signal):
    event = _event("e1", source_text, "1")
    unit = _planner().build_units_for_context("ctx", [event])[0]
    canonical_content = serialize_semantic_unit(unit["segments"], [source_text], title_text="")
    planner_result = build_final_tagger_prompt("semantic-hybrid-v3", canonical_content, ["e1"])
    worker_result = build_worker_final_prompt(
        {
            "prompt_version": "semantic-hybrid-v3",
            "attempt_count": 1,
            "retry_reason": None,
        },
        SimpleNamespace(content=canonical_content, event_ids=("e1",)),
    )

    assert planner_result.prompt.encode("utf-8") == worker_result.prompt.encode("utf-8")
    for signal_name in ("contains_code", "contains_logs", "contains_urls"):
        assert getattr(planner_result.signals, signal_name) is (signal_name == expected_signal)


def test_sliced_chunk_uses_byte_identical_shared_prompt_without_surrounding_text():
    source_text = "BEFORE|```code``` ERROR https://example.invalid|AFTER"
    start = source_text.index("```")
    end = source_text.index("|AFTER")
    event = _event("e1", source_text, "1")
    unit = _planner()._prepare_unit("ctx", [planner_segment(event, start, end)], "", sequence_no=1)
    sliced = source_text[start:end]
    canonical_content = serialize_semantic_unit(unit["segments"], [sliced])
    planner_result = build_final_tagger_prompt("semantic-hybrid-v3", canonical_content, ["e1"])
    worker_result = build_worker_final_prompt(
        {"prompt_version": "semantic-hybrid-v3", "attempt_count": 1},
        SimpleNamespace(content=canonical_content, event_ids=("e1",)),
    )

    assert planner_result.prompt.encode("utf-8") == worker_result.prompt.encode("utf-8")
    assert "BEFORE" not in planner_result.prompt
    assert "AFTER" not in planner_result.prompt


def test_worker_uses_byte_identical_budgeted_prompt_for_every_retry_variant():
    source_text = "BEFORE|```code``` ERROR https://example.invalid|AFTER"
    sliced = source_text[source_text.index("```") : source_text.index("|AFTER")]
    manifest = {
        "title_included": False,
        "segments": [{"event_id": "e1", "role": "user"}],
    }
    canonical_content = serialize_semantic_unit(manifest, [sliced])
    budgeted = build_supported_final_prompt_variants(
        "semantic-hybrid-v3", canonical_content, ["e1"]
    )
    unit = SimpleNamespace(content=canonical_content, event_ids=("e1",))

    for variant in SUPPORTED_PROMPT_VARIANTS:
        worker_result = build_worker_final_prompt(
            {
                "prompt_version": "semantic-hybrid-v3",
                "attempt_count": variant.attempt_no,
                "retry_reason": variant.retry_reason,
            },
            unit,
        )
        assert worker_result.prompt.encode("utf-8") == budgeted[variant.name].prompt.encode("utf-8")
        assert worker_result.evidence_alias_to_event_id == {"E1": "e1"}


def test_prompt_template_memoization_preserves_output_bytes():
    content = "[CONTEXT_TITLE]\n[/CONTEXT_TITLE]\n\n[EVENT event_id=e1 role=user]\nx\n[/EVENT]"
    _rendered_prompt_template.cache_clear()
    uncached = build_final_tagger_prompt("semantic-hybrid-v3", content, ["e1"]).prompt
    cached = build_final_tagger_prompt("semantic-hybrid-v3", content, ["e1"]).prompt
    _rendered_prompt_template.cache_clear()
    rebuilt = build_final_tagger_prompt("semantic-hybrid-v3", content, ["e1"]).prompt

    assert uncached.encode("utf-8") == cached.encode("utf-8") == rebuilt.encode("utf-8")


def test_prompt_budget_configuration_contract():
    assert PromptBudgetConfig().as_settings() == {
        "num_ctx": 8192,
        "max_prompt_tokens": 5632,
        "num_predict": 1536,
        "safety_margin": 1024,
        "prompt_estimator_version": PROMPT_ESTIMATOR_VERSION,
        "chunk_overlap_characters": 256,
        "chunk_boundary_backtrack_characters": 256,
    }
    with pytest.raises(PromptBudgetError, match="above num_ctx"):
        PromptBudgetConfig(num_ctx=8191)
    with pytest.raises(PromptBudgetError, match="Unsupported"):
        PromptBudgetConfig(prompt_estimator_version="unsupported")


def test_unit_v2_identity_and_manifest_are_frozen():
    events = [
        {
            "event_id": "e-b",
            "text": "Second synthetic",
            "timestamp_start": "2026-01-02T00:00:00Z",
            "event_type": "chatgpt_message",
            "source_role": "assistant",
        },
        {
            "event_id": "e-a",
            "text": "First synthetic",
            "timestamp_start": "2026-01-01T00:00:00Z",
            "event_type": "chatgpt_message",
            "source_role": "user",
        },
        {
            "event_id": "e-c",
            "text": "Third synthetic",
            "timestamp_start": "2026-01-03T00:00:00Z",
            "event_type": "human",
        },
    ]
    units = UnitBuilder(max_events=2, overlap_events=1).build_units_for_context(
        "ctx-regression", events, "Synthetic title"
    )

    assert [unit["unit_id"] for unit in units] == [
        "unit-99e2e9fab89c692b70e74e07fa3176f3f53564bb08a2398131ddbf2b23559acc",
        "unit-c6c8320a272180b7e8b8ff5d959c92de008609f3df71d50de0548ad4df902091",
    ]
    assert [unit["content_hash"] for unit in units] == [
        "67378d2567189581ad9415de4128fc002f5728bdfa9b26baa004da22a63be177",
        "3ebe8cafa94df6ff67cb32cec460ec1d49f53be06d2d7acff5db72c65274d0a0",
    ]
    assert units[0]["segments"] == {
        "title_included": True,
        "title_source": "ctx-regression",
        "unit_strategy_version": "unit-v2-whole-events",
        "segments": [
            {
                "event_id": "e-a",
                "context_id": "ctx-regression",
                "role": "user",
                "start_char": 0,
                "end_char": 15,
                "sequence_in_unit": 0,
                "is_overlap": False,
            },
            {
                "event_id": "e-b",
                "context_id": "ctx-regression",
                "role": "assistant",
                "start_char": 0,
                "end_char": 16,
                "sequence_in_unit": 1,
                "is_overlap": False,
            },
        ],
        "oversized_single_event": False,
    }


def test_whole_event_greedy_packing_order_roles_and_ten_event_limit():
    planner = _planner(max_events=10, overlap_events=0)
    events = [
        _event(f"e-{index:02d}", "short", f"2026-01-{index:02d}", "assistant")
        for index in range(11, 0, -1)
    ]
    units = planner.build_units_for_context("ctx", events)

    assert [len(unit["event_ids"]) for unit in units] == [10, 1]
    assert units[0]["event_ids"] == [f"e-{index:02d}" for index in range(1, 11)]
    assert {segment["role"] for segment in _segments(units)} == {"assistant"}
    assert all(unit["estimated_prompt_tokens"] <= 5632 for unit in units)


def test_next_event_forces_deterministic_boundary_and_overlap_never_oversizes():
    planner = _planner(max_prompt_tokens=2400, overlap_events=1)
    events = [
        _event("e1", "a" * 700, "1"),
        _event("e2", "b" * 700, "2"),
        _event("e3", "c" * 700, "3"),
    ]
    units = planner.build_units_for_context("ctx", events)

    assert [unit["event_ids"] for unit in units] == [["e1"], ["e2"], ["e3"]]
    assert all(unit["estimated_prompt_tokens"] <= 2400 for unit in units)


def test_complete_events_fit_together_when_complete_prompt_fits():
    units = _planner(overlap_events=0).build_units_for_context(
        "ctx",
        [_event("e1", "first", "1"), _event("e2", "second", "2")],
    )
    assert len(units) == 1
    assert units[0]["event_ids"] == ["e1", "e2"]


def _event_whose_initial_prompt_only_just_fits() -> tuple[dict, object]:
    planner = _planner()
    low = 1
    high = 20000
    best = None
    while low <= high:
        middle = (low + high) // 2
        event = _event("e-retry-boundary", "x" * middle, "1")
        assessment = planner._measure("ctx", [planner_segment(event, 0, middle)], "")[2]
        if assessment.initial_prompt_estimate <= 5632:
            best = (event, assessment)
            low = middle + 1
        else:
            high = middle - 1
    assert best is not None
    return best


def test_retry_budget_chunks_event_when_attempt_1_fits_but_retry_does_not():
    event, full_assessment = _event_whose_initial_prompt_only_just_fits()
    assert full_assessment.initial_prompt_estimate <= 5632
    assert full_assessment.maximum_retry_prompt_estimate > 5632

    planner = _planner()
    production = planner.build_units_for_context("ctx", [event])
    attempt_1_diagnostic = planner.build_attempt_1_diagnostic_structure("ctx", [event])

    assert len(attempt_1_diagnostic) == 1
    assert len(production) > 1
    assert all(unit["worst_case_prompt_estimate"] <= 5632 for unit in production)
    assert all(
        unit["estimated_token_count"] == unit["worst_case_prompt_estimate"] for unit in production
    )
    assert all(
        unit["segments"]["prompt_budget"]
        == {
            "initial_prompt_estimate": unit["initial_prompt_estimate"],
            "maximum_retry_prompt_estimate": unit["maximum_retry_prompt_estimate"],
            "worst_case_prompt_estimate": unit["worst_case_prompt_estimate"],
            "maximum_retry_variant": unit["maximum_retry_variant"],
            "worst_case_prompt_variant": unit["worst_case_prompt_variant"],
            "variant_estimates": unit["variant_prompt_estimates"],
        }
        for unit in production
    )


def test_whole_event_overlap_is_preserved_only_as_a_fitting_nonduplicate_unit():
    units = _planner(max_events=2, overlap_events=1).build_units_for_context(
        "ctx",
        [
            _event("e1", "first", "1"),
            _event("e2", "second", "2"),
            _event("e3", "third", "3"),
        ],
    )

    assert [unit["event_ids"] for unit in units] == [["e1", "e2"], ["e2", "e3"]]
    assert units[0]["unit_id"] != units[1]["unit_id"]
    assert units[1]["segments"]["segments"][0]["is_overlap"] is True
    assert all(unit["estimated_prompt_tokens"] <= 5632 for unit in units)


def test_oversized_event_chunks_cover_text_with_deterministic_overlap_and_unicode():
    text = ("zażółć 🐦 漢字 bezpieczny-fragment " * 900).strip()
    planner = _planner()
    first = planner.build_units_for_context("ctx", [_event("e1", text, "1")])
    second = planner.build_units_for_context("ctx", [_event("e1", text, "1")])
    segments = _segments(first)

    assert len(first) > 1
    assert [(unit["unit_id"], unit["content_hash"]) for unit in first] == [
        (unit["unit_id"], unit["content_hash"]) for unit in second
    ]
    assert segments[0]["start_char"] == 0
    assert segments[-1]["end_char"] == len(text)
    assert all(segment["end_char"] > segment["start_char"] for segment in segments)
    assert all(unit["estimated_prompt_tokens"] <= 5632 for unit in first)
    for previous, current in zip(segments, segments[1:]):
        assert previous["end_char"] - current["start_char"] == 256
        assert current["start_char"] > previous["start_char"]
    assert (
        "".join(
            text[segment["start_char"] : segment["end_char"]]
            if index == 0
            else text[segments[index - 1]["end_char"] : segment["end_char"]]
            for index, segment in enumerate(segments)
        )
        == text
    )


def test_whitespace_free_chunking_makes_progress_and_has_short_final_chunk():
    text = "x" * 25000
    units = _planner(backtrack=256).build_units_for_context("ctx", [_event("e1", text, "1")])
    segments = _segments(units)
    lengths = [segment["end_char"] - segment["start_char"] for segment in segments]

    assert len(units) > 1
    assert all(length > 0 for length in lengths)
    assert lengths[-1] < lengths[0]
    assert segments[-1]["end_char"] == len(text)


def test_binary_search_largest_fit_and_boundary_preferences():
    planner = _planner(max_prompt_tokens=2200)
    base = _event("e1", "x" * 3000, "1")
    hard = planner._largest_fitting_end("ctx", base, "", 0, len(base["text"]))
    assert planner._fits(
        "ctx",
        [planner_segment(base, 0, hard)],
        "",
    )
    assert not planner._fits(
        "ctx",
        [planner_segment(base, 0, hard + 1)],
        "",
    )

    newline_text = "x" * (hard - 20) + "\n" + "x" * (3000 - hard + 19)
    whitespace_text = "x" * (hard - 20) + " " + "x" * (3000 - hard + 19)
    assert planner._preferred_boundary(newline_text, 0, hard) == hard - 19
    assert planner._preferred_boundary(whitespace_text, 0, hard) == hard - 19
    assert planner._preferred_boundary("x" * 3000, 0, hard) == hard


def planner_segment(event: dict, start: int, end: int):
    from scripts.semantic_tagger.unit_planner import _SegmentInput

    return _SegmentInput(event=event, start_char=start, end_char=end, is_chunk=True)


def test_ranges_roles_and_overlap_metadata_change_v3_identity():
    planner = _planner()
    event = _event("e1", "synthetic middle slice", "1")
    base = planner._prepare_unit("ctx", [planner_segment(event, 0, 10)], "", 1)
    range_changed = planner._prepare_unit("ctx", [planner_segment(event, 1, 10)], "", 1)
    role_changed_event = dict(event, source_role="assistant")
    role_changed = planner._prepare_unit("ctx", [planner_segment(role_changed_event, 0, 10)], "", 1)
    overlap_changed_segment = planner_segment(event, 0, 10)
    object.__setattr__(overlap_changed_segment, "overlap_from_previous_characters", 1)
    overlap_changed = planner._prepare_unit("ctx", [overlap_changed_segment], "", 1)

    assert (
        len(
            {
                (base["unit_id"], base["content_hash"]),
                (range_changed["unit_id"], range_changed["content_hash"]),
                (role_changed["unit_id"], role_changed["content_hash"]),
                (overlap_changed["unit_id"], overlap_changed["content_hash"]),
            }
        )
        == 4
    )


def test_invalid_ranges_and_impossible_prompt_are_planning_errors():
    planner = _planner(max_prompt_tokens=1945)
    event = _event("e1", "x", "1")
    with pytest.raises(UnitPlanningError, match="no room"):
        planner.build_units_for_context("ctx", [event])

    manifest = {
        "title_included": False,
        "segments": [{"event_id": "e1", "role": "user"}],
    }
    assert "x" in serialize_semantic_unit(manifest, ["x"])
