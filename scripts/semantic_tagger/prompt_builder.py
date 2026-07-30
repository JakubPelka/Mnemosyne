import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel
from scripts.semantic_tagger.schemas import TaggerOutput
from typing import Dict

PROMPT_MAP = {
    "semantic-hybrid-v1": "prompts/semantic_hybrid_v1.md",
    "semantic-hybrid-v2": "prompts/semantic_hybrid_v2.md",
    "semantic-hybrid-v3": "prompts/semantic_hybrid_v3.md",
}
PROMPT_ROOT = Path(__file__).resolve().parents[2]


class PromptBuildResult(BaseModel):
    prompt: str
    evidence_alias_to_event_id: Dict[str, str]


class ContentSignals(BaseModel):
    contains_code: bool
    contains_logs: bool
    contains_urls: bool


class FinalPromptBuildResult(PromptBuildResult):
    signals: ContentSignals


class PromptBuildError(ValueError):
    pass


@dataclass(frozen=True)
class SupportedPromptVariant:
    """One prompt shape the worker can send for a semantic-tagger job."""

    name: str
    attempt_no: int
    retry_reason: str | None


SUPPORTED_PROMPT_VARIANTS = (
    SupportedPromptVariant("attempt_1", 1, None),
    SupportedPromptVariant("attempt_2_schema_retry", 2, None),
    SupportedPromptVariant("attempt_2_facets_missing", 2, "facets_missing"),
    SupportedPromptVariant("attempt_3_reduced_output", 3, None),
)


@lru_cache(maxsize=len(PROMPT_MAP))
def _rendered_prompt_template(prompt_version: str) -> str:
    prompt_path = PROMPT_ROOT / PROMPT_MAP[prompt_version]
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")

    with open(prompt_path, "r", encoding="utf-8") as handle:
        template = handle.read()

    if prompt_version == "semantic-hybrid-v3":
        from scripts.semantic_tagger.schemas import TaggerOutputV3ModelOutput

        schema_json = json.dumps(TaggerOutputV3ModelOutput.model_json_schema(), indent=2)
    else:
        schema_json = json.dumps(TaggerOutput.model_json_schema(), indent=2)
    return template.replace("{schema_json}", schema_json)


def build_tagger_prompt(
    prompt_version: str,
    contains_code: bool,
    contains_logs: bool,
    contains_urls: bool,
    content: str,
    event_ids: list[str] = None,
) -> PromptBuildResult:
    if prompt_version not in PROMPT_MAP:
        raise ValueError(f"Unknown prompt version: {prompt_version}")

    evidence_alias_to_event_id = {}

    if prompt_version == "semantic-hybrid-v3":
        import re

        def repl(match):
            event_id = match.group(1)
            # Make sure we reuse aliases for the same event_id
            for alias, eid in evidence_alias_to_event_id.items():
                if eid == event_id:
                    return f"[EVENT evidence_id={alias} "

            next_idx = len(evidence_alias_to_event_id) + 1
            alias = f"E{next_idx}"
            evidence_alias_to_event_id[alias] = event_id
            return f"[EVENT evidence_id={alias} "

        content = re.sub(r"\[EVENT event_id=([a-zA-Z0-9_-]+)\s+", repl, content)

        if event_ids:
            # Validate
            unique_mapped = set(evidence_alias_to_event_id.values())
            unique_expected = set(event_ids)
            if unique_mapped != unique_expected:
                raise PromptBuildError("Alias mapping failed: missing or extra events.")

            for i in range(1, len(evidence_alias_to_event_id) + 1):
                if f"E{i}" not in evidence_alias_to_event_id:
                    raise PromptBuildError(f"Alias sequence broken: missing E{i}")

            for eid in event_ids:
                if f"event_id={eid}" in content:
                    raise PromptBuildError(f"Event ID {eid} leaked in prompt.")

    prompt = _rendered_prompt_template(prompt_version)

    signals = []
    if contains_code:
        signals.append("contains_code: true")
    if contains_logs:
        signals.append("contains_logs: true")
    if contains_urls:
        signals.append("contains_urls: true")
    signals_str = ", ".join(signals) if signals else "none"

    final_prompt = f"{prompt}\n\nSygnały wejścia: {signals_str}\n\nOto treść wejściowa do przeanalizowania:\n\n{content}"
    return PromptBuildResult(
        prompt=final_prompt, evidence_alias_to_event_id=evidence_alias_to_event_id
    )


def analyze_content_signals(content: str) -> ContentSignals:
    return ContentSignals(
        contains_code="```" in content or "def " in content or "function" in content,
        contains_logs="ERROR" in content or "WARN" in content or "Traceback" in content,
        contains_urls="http://" in content or "https://" in content,
    )


def build_final_tagger_prompt(
    prompt_version: str,
    content: str,
    event_ids: list[str],
    *,
    attempt_no: int = 1,
    retry_reason: str | None = None,
) -> FinalPromptBuildResult:
    """Build the exact final prompt used for estimation and model requests."""
    signals = analyze_content_signals(content)
    result = build_tagger_prompt(
        prompt_version,
        signals.contains_code,
        signals.contains_logs,
        signals.contains_urls,
        content,
        event_ids,
    )
    prompt = result.prompt
    if attempt_no == 2:
        if retry_reason == "facets_missing":
            prompt += (
                "\n\nThe previous response omitted required facets.\n"
                "Return fewer concepts if necessary rather than inventing generic facets.\n"
                "Every concept must contain 1 to 3 entity_types and 1 to 5 domains.\n"
                "Empty arrays [] are strictly invalid for these fields."
            )
        else:
            prompt += (
                "\n\nOstatnia próba zakończyła się błędem schematu. "
                "Zwróć tylko 100% poprawne dane, używając poprawnego JSON."
            )
    elif attempt_no == 3:
        prompt += (
            "\n\nOstatnia próba zakończyła się błędem schematu. "
            "Zwróć maksymalnie 6 pojęć i 0 relacji."
        )
    return FinalPromptBuildResult(
        prompt=prompt,
        evidence_alias_to_event_id=result.evidence_alias_to_event_id,
        signals=signals,
    )


def build_supported_final_prompt_variants(
    prompt_version: str,
    content: str,
    event_ids: list[str],
) -> dict[str, FinalPromptBuildResult]:
    """Build every prompt variant that the worker can send for one unit."""
    return {
        variant.name: build_final_tagger_prompt(
            prompt_version,
            content,
            event_ids,
            attempt_no=variant.attempt_no,
            retry_reason=variant.retry_reason,
        )
        for variant in SUPPORTED_PROMPT_VARIANTS
    }


def supported_prompt_variant_name(attempt_no: int, retry_reason: str | None) -> str:
    """Resolve worker retry state to one of the explicitly budgeted variants."""
    if attempt_no == 1:
        return "attempt_1"
    if attempt_no == 2:
        if retry_reason == "facets_missing":
            return "attempt_2_facets_missing"
        return "attempt_2_schema_retry"
    if attempt_no == 3:
        return "attempt_3_reduced_output"
    raise PromptBuildError(f"Unsupported semantic-tagger attempt: {attempt_no}")
