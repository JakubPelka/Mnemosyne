import json
from dataclasses import dataclass
from typing import Any

from scripts.semantic_tagger.privacy import safe_hash
from scripts.semantic_tagger.prompt_budget import (
    PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
    PromptBudgetConfig,
    PromptVariantBudget,
    estimate_supported_prompt_variants,
)
from scripts.semantic_tagger.unit_serializer import (
    compute_v3_content_hash,
    serialize_semantic_unit,
)


PROMPT_BUDGET_STRATEGY_VERSION = "unit-v3-prompt-budgeted-chunks"
_BUDGET_ATTEMPT_1_DIAGNOSTIC = "attempt_1_diagnostic"


class UnitPlanningError(ValueError):
    """A deterministic preparation error; it is never an inference failure."""


@dataclass(frozen=True)
class _SegmentInput:
    event: dict[str, Any]
    start_char: int
    end_char: int
    is_overlap: bool = False
    is_chunk: bool = False
    overlap_from_previous_characters: int = 0


def resolve_event_role(event: dict[str, Any]) -> str:
    source_role = event.get("source_role")
    if source_role in ("assistant", "user", "system", "tool"):
        return source_role
    event_type = event.get("event_type")
    if event_type in ("assistant", "user", "system", "tool"):
        return event_type
    if event_type == "human":
        return "user"
    if event_type == "bot":
        return "assistant"
    return "unknown"


def plan_manifest_context_once(
    planner: "PromptBudgetUnitPlanner",
    context_id: str,
    canonical_events: list[dict[str, Any]],
    title: str,
    source_event_id_groups: list[list[str]],
    *,
    attempt_1_diagnostic: bool = False,
) -> list[dict[str, Any]]:
    """Plan Contract A: ordered event union, exactly one planner call per context.

    Overlapping source v2 units contribute selection provenance only. Their
    shared events are selected once in canonical timestamp/event-ID order and
    cannot create duplicate v3 units or jobs through repeated planning.
    """
    wanted = {event_id for event_ids in source_event_id_groups for event_id in event_ids}
    selected = [event for event in canonical_events if event.get("event_id") in wanted]
    selected_ids = {event["event_id"] for event in selected}
    if selected_ids != wanted:
        missing_count = len(wanted - selected_ids)
        raise UnitPlanningError(
            f"Manifest selection contains {missing_count} events absent from its canonical context"
        )
    if attempt_1_diagnostic:
        return planner.build_attempt_1_diagnostic_structure(context_id, selected, title)
    return planner.build_units_for_context(context_id, selected, title)


class PromptBudgetUnitPlanner:
    def __init__(
        self,
        *,
        prompt_version: str,
        schema_version: str,
        strategy_version: str = PROMPT_BUDGET_STRATEGY_VERSION,
        budget: PromptBudgetConfig | None = None,
        max_events: int = 10,
        overlap_events: int = 1,
    ):
        if strategy_version != PROMPT_BUDGET_STRATEGY_VERSION:
            raise UnitPlanningError(f"Unsupported planner strategy: {strategy_version}")
        if max_events < 1:
            raise UnitPlanningError("max_events must be at least 1")
        if overlap_events < 0:
            raise UnitPlanningError("overlap_events must be non-negative")
        self.prompt_version = prompt_version
        self.schema_version = schema_version
        self.strategy_version = strategy_version
        self.budget = budget or PromptBudgetConfig()
        self.max_events = max_events
        self.overlap_events = overlap_events

    def build_units_for_context(
        self,
        context_id: str,
        events: list[dict[str, Any]],
        title: str = "",
    ) -> list[dict[str, Any]]:
        """Build queueable units budgeted against every supported worker prompt."""
        return self._build_units_for_context(
            context_id,
            events,
            title,
            budget_basis=PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
        )

    def build_attempt_1_diagnostic_structure(
        self,
        context_id: str,
        events: list[dict[str, Any]],
        title: str = "",
    ) -> list[dict[str, Any]]:
        """Build a non-queueable baseline used only to measure retry-budget effects."""
        return self._build_units_for_context(
            context_id,
            events,
            title,
            budget_basis=_BUDGET_ATTEMPT_1_DIAGNOSTIC,
        )

    def _build_units_for_context(
        self,
        context_id: str,
        events: list[dict[str, Any]],
        title: str,
        *,
        budget_basis: str,
    ) -> list[dict[str, Any]]:
        if budget_basis not in {
            PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
            _BUDGET_ATTEMPT_1_DIAGNOSTIC,
        }:
            raise UnitPlanningError(f"Unsupported prompt budget basis: {budget_basis}")
        ordered = sorted(
            (dict(event) for event in events),
            key=lambda event: (
                event.get("timestamp_start") or "",
                event.get("event_id") or "",
            ),
        )
        self._validate_events(context_id, ordered)

        units: list[dict[str, Any]] = []
        current: list[_SegmentInput] = []
        carry: list[_SegmentInput] = []
        index = 0

        while index < len(ordered):
            event = ordered[index]
            text = event.get("text") or ""
            whole = _SegmentInput(event=event, start_char=0, end_char=len(text))

            if not self._fits(context_id, [whole], title, budget_basis=budget_basis):
                if current:
                    units.append(
                        self._prepare_unit(
                            context_id,
                            current,
                            title,
                            len(units) + 1,
                            budget_basis=budget_basis,
                        )
                    )
                    carry = self._whole_event_carry(current)
                    current = []
                    continue
                units.extend(
                    self._chunk_event(
                        context_id,
                        event,
                        title,
                        first_sequence_no=len(units) + 1,
                        budget_basis=budget_basis,
                    )
                )
                carry = []
                index += 1
                continue

            if not current:
                candidate = [*carry, whole]
                if (
                    carry
                    and len(candidate) <= self.max_events
                    and self._fits(
                        context_id,
                        candidate,
                        title,
                        budget_basis=budget_basis,
                    )
                ):
                    current = candidate
                else:
                    current = [whole]
                carry = []
                index += 1
                continue

            candidate = [*current, whole]
            if len(candidate) <= self.max_events and self._fits(
                context_id,
                candidate,
                title,
                budget_basis=budget_basis,
            ):
                current = candidate
                index += 1
                continue

            units.append(
                self._prepare_unit(
                    context_id,
                    current,
                    title,
                    len(units) + 1,
                    budget_basis=budget_basis,
                )
            )
            carry = self._whole_event_carry(current)
            current = []

        if current:
            units.append(
                self._prepare_unit(
                    context_id,
                    current,
                    title,
                    len(units) + 1,
                    budget_basis=budget_basis,
                )
            )

        if budget_basis == PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS:
            for unit in units:
                estimate = unit["worst_case_prompt_estimate"]
                if estimate > self.budget.max_prompt_tokens:
                    raise UnitPlanningError(
                        f"Planner invariant violated: worst-case prompt {estimate} exceeds "
                        f"{self.budget.max_prompt_tokens}"
                    )
        return units

    def _validate_events(self, context_id: str, events: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        for event in events:
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise UnitPlanningError("Every source event must have a non-empty event_id")
            if event_id in seen:
                raise UnitPlanningError(
                    f"Duplicate canonical event_id in context {safe_hash(context_id)}"
                )
            seen.add(event_id)
            event_context = event.get("context_id")
            if event_context is not None and event_context != context_id:
                raise UnitPlanningError(
                    f"Event context mismatch in context {safe_hash(context_id)}"
                )
            text = event.get("text")
            if text is not None and not isinstance(text, str):
                raise UnitPlanningError(f"Event {safe_hash(event_id)} has non-text content")

    def _whole_event_carry(self, segments: list[_SegmentInput]) -> list[_SegmentInput]:
        if any(segment.is_chunk for segment in segments):
            return []
        carry_len = max(
            0,
            min(self.overlap_events, self.max_events - 1, len(segments) - 1),
        )
        if carry_len == 0:
            return []
        return [
            _SegmentInput(
                event=segment.event,
                start_char=segment.start_char,
                end_char=segment.end_char,
                is_overlap=True,
            )
            for segment in segments[-carry_len:]
        ]

    def _chunk_event(
        self,
        context_id: str,
        event: dict[str, Any],
        title: str,
        *,
        first_sequence_no: int,
        budget_basis: str,
    ) -> list[dict[str, Any]]:
        text = event.get("text") or ""
        if not text:
            raise UnitPlanningError("An empty event cannot require text chunking")

        chunks: list[dict[str, Any]] = []
        start = 0
        previous_end: int | None = None
        while start < len(text):
            hard_end = self._largest_fitting_end(
                context_id,
                event,
                title,
                start,
                len(text),
                budget_basis=budget_basis,
            )
            end = self._preferred_boundary(text, start, hard_end)
            if end <= start:
                raise UnitPlanningError("Chunk planning made no forward progress")

            overlap = 0 if previous_end is None else previous_end - start
            segment = _SegmentInput(
                event=event,
                start_char=start,
                end_char=end,
                is_overlap=overlap > 0,
                is_chunk=True,
                overlap_from_previous_characters=overlap,
            )
            chunks.append(
                self._prepare_unit(
                    context_id,
                    [segment],
                    title,
                    first_sequence_no + len(chunks),
                    chunk_index=len(chunks),
                    budget_basis=budget_basis,
                )
            )
            if end == len(text):
                break

            chunk_length = end - start
            effective_overlap = min(
                self.budget.chunk_overlap_characters,
                max(0, chunk_length - 1),
            )
            next_start = end - effective_overlap
            if next_start <= start:
                next_start = start + 1
            if next_start >= end and end < len(text):
                next_start = end
            if next_start <= start:
                raise UnitPlanningError("Chunk overlap prevented forward progress")
            previous_end = end
            start = next_start

        return chunks

    def _largest_fitting_end(
        self,
        context_id: str,
        event: dict[str, Any],
        title: str,
        start: int,
        text_length: int,
        *,
        budget_basis: str = PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
    ) -> int:
        low = start + 1
        high = text_length
        best: int | None = None
        while low <= high:
            middle = (low + high) // 2
            segment = _SegmentInput(
                event=event,
                start_char=start,
                end_char=middle,
                is_chunk=True,
            )
            if self._fits(
                context_id,
                [segment],
                title,
                budget_basis=budget_basis,
            ):
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best is None:
            raise UnitPlanningError("Prompt wrapper leaves no room for even one source character")
        return best

    def _preferred_boundary(self, text: str, start: int, hard_end: int) -> int:
        if hard_end == len(text) or self.budget.chunk_boundary_backtrack_characters == 0:
            return hard_end
        window_start = max(
            start,
            hard_end - self.budget.chunk_boundary_backtrack_characters,
        )
        newline = text.rfind("\n", window_start, hard_end)
        if newline >= start:
            return newline + 1
        for position in range(hard_end - 1, window_start - 1, -1):
            if text[position].isspace():
                return position + 1
        return hard_end

    def _fits(
        self,
        context_id: str,
        segments: list[_SegmentInput],
        title: str,
        *,
        budget_basis: str = PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
    ) -> bool:
        assessment = self._measure(context_id, segments, title)[2]
        if budget_basis == PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS:
            estimate = assessment.worst_case_prompt_estimate
        elif budget_basis == _BUDGET_ATTEMPT_1_DIAGNOSTIC:
            estimate = assessment.initial_prompt_estimate
        else:
            raise UnitPlanningError(f"Unsupported prompt budget basis: {budget_basis}")
        return estimate <= self.budget.max_prompt_tokens

    def _measure(
        self,
        context_id: str,
        segments: list[_SegmentInput],
        title: str,
    ) -> tuple[dict[str, Any], str, PromptVariantBudget]:
        if not segments:
            raise UnitPlanningError("A prepared unit must contain at least one segment")
        for segment in segments:
            text_length = len(segment.event.get("text") or "")
            if not (0 <= segment.start_char <= segment.end_char <= text_length):
                raise UnitPlanningError("Invalid source character range")
            if segment.is_chunk and segment.start_char == segment.end_char:
                raise UnitPlanningError("Chunk ranges must not be empty")
        manifest = self._manifest(context_id, segments, title)
        sliced_texts = [
            (segment.event.get("text") or "")[segment.start_char : segment.end_char]
            for segment in segments
        ]
        canonical_content = serialize_semantic_unit(manifest, sliced_texts, title_text=title)
        event_ids = list(dict.fromkeys(segment.event["event_id"] for segment in segments))
        assessment = estimate_supported_prompt_variants(
            self.prompt_version,
            canonical_content,
            event_ids,
            self.budget.prompt_estimator_version,
            self.budget.prompt_estimator_contract,
        )
        return manifest, canonical_content, assessment

    def _manifest(
        self,
        context_id: str,
        segments: list[_SegmentInput],
        title: str,
    ) -> dict[str, Any]:
        manifest = {
            "title_included": bool(title),
            "title_source": context_id,
            "unit_strategy_version": self.strategy_version,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "prompt_estimator_version": self.budget.prompt_estimator_version,
            "chunk_overlap_characters": self.budget.chunk_overlap_characters,
            "chunk_boundary_backtrack_characters": (
                self.budget.chunk_boundary_backtrack_characters
            ),
            "segments": [
                {
                    "event_id": segment.event["event_id"],
                    "context_id": context_id,
                    "role": resolve_event_role(segment.event),
                    "start_char": segment.start_char,
                    "end_char": segment.end_char,
                    "sequence_in_unit": index,
                    "is_overlap": segment.is_overlap,
                    "is_chunk": segment.is_chunk,
                    "overlap_from_previous_characters": (segment.overlap_from_previous_characters),
                }
                for index, segment in enumerate(segments)
            ],
            "oversized_single_event": any(segment.is_chunk for segment in segments),
        }
        if self.budget.prompt_estimator_contract is not None:
            manifest["prompt_estimator_contract"] = (
                self.budget.prompt_estimator_contract.as_manifest()
            )
        return manifest

    def _prepare_unit(
        self,
        context_id: str,
        segments: list[_SegmentInput],
        title: str,
        sequence_no: int,
        chunk_index: int | None = None,
        *,
        budget_basis: str = PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
    ) -> dict[str, Any]:
        manifest, canonical_content, assessment = self._measure(context_id, segments, title)
        selected_estimate = (
            assessment.worst_case_prompt_estimate
            if budget_basis == PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS
            else assessment.initial_prompt_estimate
        )
        if selected_estimate > self.budget.max_prompt_tokens:
            raise UnitPlanningError(
                "Prepared prompt estimate "
                f"{selected_estimate} exceeds "
                f"{self.budget.max_prompt_tokens}"
            )
        manifest["prompt_budget_basis"] = budget_basis
        manifest["prompt_budget"] = assessment.as_manifest()
        content_hash = compute_v3_content_hash(
            self.schema_version,
            self.strategy_version,
            manifest,
            canonical_content,
        )
        # Intentional contract: v3 unit identity is sequence-sensitive. This
        # makes a changed earlier boundary invalidate later positional IDs,
        # while content_hash remains the content/range provenance component.
        identity = {
            "strategy_version": self.strategy_version,
            "context_id": context_id,
            "sequence_no": sequence_no,
            "ordered_event_ids": [segment.event["event_id"] for segment in segments],
            "roles": [resolve_event_role(segment.event) for segment in segments],
            "ranges": [[segment.start_char, segment.end_char] for segment in segments],
            "overlap": [
                {
                    "event_overlap": segment.is_overlap,
                    "character_overlap": segment.overlap_from_previous_characters,
                }
                for segment in segments
            ],
            "content_hash": content_hash,
        }
        unit_id = f"unit-{safe_hash(json.dumps(identity, sort_keys=True, separators=(',', ':')))}"
        event_ids = list(dict.fromkeys(segment.event["event_id"] for segment in segments))
        character_count = sum(segment.end_char - segment.start_char for segment in segments)
        result = {
            "unit_id": unit_id,
            "context_id": context_id,
            "sequence_no": sequence_no,
            "content_hash": content_hash,
            "event_ids": event_ids,
            "segments": manifest,
            "event_count": len(event_ids),
            "character_count": character_count,
            # The database column keeps the authoritative queue budget: the
            # maximum across every prompt attempt the worker can issue.
            "estimated_token_count": assessment.worst_case_prompt_estimate,
            "estimated_prompt_tokens": assessment.worst_case_prompt_estimate,
            "initial_prompt_estimate": assessment.initial_prompt_estimate,
            "maximum_retry_prompt_estimate": (assessment.maximum_retry_prompt_estimate),
            "worst_case_prompt_estimate": assessment.worst_case_prompt_estimate,
            "maximum_retry_variant": assessment.maximum_retry_variant,
            "worst_case_prompt_variant": assessment.worst_case_prompt_variant,
            "variant_prompt_estimates": dict(assessment.variant_estimates),
            "first_event_at": segments[0].event.get("timestamp_start"),
            "last_event_at": segments[-1].event.get("timestamp_start"),
            "title": title,
            "contains_chunked_text": any(segment.is_chunk for segment in segments),
        }
        if chunk_index is not None:
            result["chunk_index"] = chunk_index
        return result
