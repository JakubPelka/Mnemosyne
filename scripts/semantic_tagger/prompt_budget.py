from dataclasses import dataclass
from typing import Any

from scripts.semantic_tagger.prompt_builder import (
    SUPPORTED_PROMPT_VARIANTS,
    build_supported_final_prompt_variants,
)

PROMPT_ESTIMATOR_VERSION = "prompt-estimator-v2-utf8-13-over-40"
PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS = "all_supported_attempts"
DEFAULT_NUM_CTX = 8192
DEFAULT_MAX_PROMPT_TOKENS = 5632
DEFAULT_NUM_PREDICT = 1536
DEFAULT_SAFETY_MARGIN = 1024
DEFAULT_CHUNK_OVERLAP_CHARACTERS = 256
DEFAULT_CHUNK_BOUNDARY_BACKTRACK_CHARACTERS = 256


class PromptBudgetError(ValueError):
    """Raised when prompt-budget configuration or estimation is invalid."""


@dataclass(frozen=True)
class PromptBudgetConfig:
    num_ctx: int = DEFAULT_NUM_CTX
    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS
    num_predict: int = DEFAULT_NUM_PREDICT
    safety_margin: int = DEFAULT_SAFETY_MARGIN
    prompt_estimator_version: str = PROMPT_ESTIMATOR_VERSION
    prompt_estimator_contract: Any | None = None
    chunk_overlap_characters: int = DEFAULT_CHUNK_OVERLAP_CHARACTERS
    chunk_boundary_backtrack_characters: int = DEFAULT_CHUNK_BOUNDARY_BACKTRACK_CHARACTERS

    def __post_init__(self) -> None:
        from scripts.semantic_tagger.qwen3_tokenizer import (
            EXACT_PROMPT_ESTIMATOR_VERSION,
            ExactTokenizerContract,
        )

        if self.prompt_estimator_version not in {
            PROMPT_ESTIMATOR_VERSION,
            EXACT_PROMPT_ESTIMATOR_VERSION,
        }:
            raise PromptBudgetError(
                f"Unsupported prompt estimator: {self.prompt_estimator_version}"
            )
        if self.prompt_estimator_version == PROMPT_ESTIMATOR_VERSION:
            if self.prompt_estimator_contract is not None:
                raise PromptBudgetError("The v2 byte estimator must not carry a tokenizer contract")
        elif not isinstance(self.prompt_estimator_contract, ExactTokenizerContract):
            raise PromptBudgetError(
                "The exact prompt estimator requires a resolved pinned tokenizer contract"
            )
        elif self.prompt_estimator_contract.estimator_version != self.prompt_estimator_version:
            raise PromptBudgetError("Prompt estimator version and tokenizer contract disagree")
        for field_name in (
            "num_ctx",
            "max_prompt_tokens",
            "num_predict",
            "safety_margin",
        ):
            if getattr(self, field_name) < 0:
                raise PromptBudgetError(f"{field_name} must be non-negative")
        if self.num_ctx == 0 or self.max_prompt_tokens == 0:
            raise PromptBudgetError("num_ctx and max_prompt_tokens must be positive")
        if self.max_prompt_tokens > DEFAULT_MAX_PROMPT_TOKENS:
            raise PromptBudgetError(
                f"max_prompt_tokens may not exceed {DEFAULT_MAX_PROMPT_TOKENS} for this strategy"
            )
        if self.chunk_overlap_characters < 0:
            raise PromptBudgetError("chunk_overlap_characters must be non-negative")
        if self.chunk_boundary_backtrack_characters < 0:
            raise PromptBudgetError("chunk_boundary_backtrack_characters must be non-negative")
        allocated = self.max_prompt_tokens + self.num_predict + self.safety_margin
        if allocated > self.num_ctx:
            raise PromptBudgetError(
                "Invalid prompt budget: max_prompt_tokens + num_predict + safety_margin "
                f"is {allocated}, above num_ctx={self.num_ctx}"
            )

    def as_settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "num_ctx": self.num_ctx,
            "max_prompt_tokens": self.max_prompt_tokens,
            "num_predict": self.num_predict,
            "safety_margin": self.safety_margin,
            "prompt_estimator_version": self.prompt_estimator_version,
            "chunk_overlap_characters": self.chunk_overlap_characters,
            "chunk_boundary_backtrack_characters": (self.chunk_boundary_backtrack_characters),
        }
        if self.prompt_estimator_contract is not None:
            settings["prompt_estimator_contract"] = self.prompt_estimator_contract.as_manifest()
        return settings


def resolve_prompt_estimator_contract(
    estimator_version: str,
    model_name: str,
    *,
    persisted_contract: dict[str, Any] | None = None,
):
    """Resolve v3 exact metadata and optionally match persisted path-free pins."""
    if estimator_version == PROMPT_ESTIMATOR_VERSION:
        if persisted_contract is not None:
            raise PromptBudgetError("The v2 byte estimator has unexpected tokenizer metadata")
        return None

    from scripts.semantic_tagger.qwen3_tokenizer import (
        EXACT_PROMPT_ESTIMATOR_VERSION,
        ExactTokenizerContractError,
        resolve_exact_tokenizer_contract,
    )

    if estimator_version != EXACT_PROMPT_ESTIMATOR_VERSION:
        raise PromptBudgetError(f"Unsupported prompt estimator: {estimator_version}")
    try:
        contract = resolve_exact_tokenizer_contract(model_name)
    except ExactTokenizerContractError as error:
        raise PromptBudgetError(f"Exact prompt estimator is unavailable: {error}") from error
    if persisted_contract is not None and persisted_contract != contract.as_manifest():
        raise PromptBudgetError(
            "Persisted exact prompt-estimator contract does not match the local pinned model"
        )
    return contract


def estimate_prompt_tokens(
    final_prompt: str,
    estimator_version: str = PROMPT_ESTIMATOR_VERSION,
    estimator_contract=None,
) -> int:
    """Estimate a complete final prompt using the immutable calibrated contract."""
    if estimator_version == PROMPT_ESTIMATOR_VERSION:
        if estimator_contract is not None:
            raise PromptBudgetError("The v2 byte estimator must not use a tokenizer contract")
        utf8_byte_count = len(final_prompt.encode("utf-8"))
        return (13 * utf8_byte_count + 39) // 40

    from scripts.semantic_tagger.qwen3_tokenizer import (
        EXACT_PROMPT_ESTIMATOR_VERSION,
        ExactTokenizerContract,
        ExactTokenizerContractError,
        estimate_server_prompt_tokens,
    )

    if estimator_version != EXACT_PROMPT_ESTIMATOR_VERSION:
        raise PromptBudgetError(f"Unsupported prompt estimator: {estimator_version}")
    if not isinstance(estimator_contract, ExactTokenizerContract):
        raise PromptBudgetError(
            "Exact prompt estimation requires the resolved pinned tokenizer contract"
        )
    try:
        return estimate_server_prompt_tokens(final_prompt, estimator_contract)
    except ExactTokenizerContractError as error:
        raise PromptBudgetError(f"Exact prompt estimation failed: {error}") from error


@dataclass(frozen=True)
class PromptVariantBudget:
    """Token estimates for all prompt attempts supported by the worker."""

    variant_estimates: dict[str, int]
    initial_prompt_estimate: int
    maximum_retry_prompt_estimate: int
    worst_case_prompt_estimate: int
    maximum_retry_variant: str
    worst_case_prompt_variant: str

    def as_manifest(self) -> dict[str, int | str | dict[str, int]]:
        return {
            "initial_prompt_estimate": self.initial_prompt_estimate,
            "maximum_retry_prompt_estimate": self.maximum_retry_prompt_estimate,
            "worst_case_prompt_estimate": self.worst_case_prompt_estimate,
            "maximum_retry_variant": self.maximum_retry_variant,
            "worst_case_prompt_variant": self.worst_case_prompt_variant,
            "variant_estimates": dict(self.variant_estimates),
        }


def estimate_supported_prompt_variants(
    prompt_version: str,
    content: str,
    event_ids: list[str],
    estimator_version: str = PROMPT_ESTIMATOR_VERSION,
    estimator_contract=None,
) -> PromptVariantBudget:
    """Estimate every worker prompt variant and return the authoritative maximum."""
    prompts = build_supported_final_prompt_variants(prompt_version, content, event_ids)
    estimates = {
        variant.name: estimate_prompt_tokens(
            prompts[variant.name].prompt,
            estimator_version,
            estimator_contract,
        )
        for variant in SUPPORTED_PROMPT_VARIANTS
    }
    retry_names = [variant.name for variant in SUPPORTED_PROMPT_VARIANTS[1:]]
    maximum_retry_variant = max(retry_names, key=lambda name: estimates[name])
    worst_case_prompt_variant = max(estimates, key=lambda name: estimates[name])
    return PromptVariantBudget(
        variant_estimates=estimates,
        initial_prompt_estimate=estimates[SUPPORTED_PROMPT_VARIANTS[0].name],
        maximum_retry_prompt_estimate=estimates[maximum_retry_variant],
        worst_case_prompt_estimate=estimates[worst_case_prompt_variant],
        maximum_retry_variant=maximum_retry_variant,
        worst_case_prompt_variant=worst_case_prompt_variant,
    )
