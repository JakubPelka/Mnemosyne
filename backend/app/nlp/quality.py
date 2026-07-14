from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from backend.app.nlp.lexicons import ALL_STOPWORDS, EXPORT_ARTIFACTS, STOPWORDS_BY_LANGUAGE

RejectionReason = Literal[
    "stopword",
    "export_artifact",
    "too_common",
    "too_short",
    "low_information",
    "invalid_token",
    "duplicate_variant",
]

_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:[-+.#][^\W_]+)*", re.UNICODE)
_ARTIFACT_VARIANT = re.compile(
    r"^(?:turn\d*(?:search|fetch|view)?|(?:file)?cite\d*|search\d*|ref\d*)$"
)
_IDENTIFIER_FRAGMENT = re.compile(r"^(?:[a-f0-9]{12,}|[a-z]+_[a-z0-9_]+)$")


@dataclass(frozen=True, slots=True)
class TermQuality:
    normalized_term: str
    ngram_size: int
    language: str | None
    score: float
    status: Literal["accepted", "rejected"]
    rejection_reason: RejectionReason | None


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def term_tokens(value: str) -> tuple[str, ...]:
    normalized = normalize_term(value)
    return tuple(_TOKEN_PATTERN.findall(normalized))


def detect_language(tokens: tuple[str, ...]) -> str | None:
    scores = {
        language: sum(token in stopwords for token in tokens)
        for language, stopwords in STOPWORDS_BY_LANGUAGE.items()
    }
    best = max(scores.values(), default=0)
    if best == 0:
        return None
    winners = [language for language, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 else None


def is_export_artifact(tokens: tuple[str, ...]) -> bool:
    return any(token in EXPORT_ARTIFACTS or _ARTIFACT_VARIANT.fullmatch(token) for token in tokens)


def assess_term(
    value: str,
    *,
    document_frequency: int,
    document_count: int,
    tfidf_score: float,
    min_document_frequency: int = 2,
    max_document_ratio: float = 0.10,
    min_characters: int = 3,
) -> TermQuality:
    normalized = normalize_term(value)
    tokens = term_tokens(normalized)
    ngram_size = len(tokens)
    language = detect_language(tokens)
    reason: RejectionReason | None = None

    compact_length = sum(len(token) for token in tokens)
    if not tokens or any(_invalid_token(token) for token in tokens):
        reason = "invalid_token"
    elif is_export_artifact(tokens):
        reason = "export_artifact"
    elif compact_length < min_characters:
        reason = "too_short"
    elif all(token in ALL_STOPWORDS for token in tokens):
        reason = "stopword"
    elif document_count and document_frequency / document_count > max_document_ratio:
        reason = "too_common"
    elif document_frequency < min_document_frequency or tfidf_score <= 0:
        reason = "low_information"

    ngram_bonus = 1.0 + 0.35 * max(0, ngram_size - 1)
    score = max(0.0, tfidf_score) * ngram_bonus * min(2.0, math.log1p(document_frequency))
    return TermQuality(
        normalized_term=normalized,
        ngram_size=ngram_size,
        language=language,
        score=score,
        status="rejected" if reason else "accepted",
        rejection_reason=reason,
    )


def phrase_suppresses_unigram(
    phrase: TermQuality,
    unigram: TermQuality,
    *,
    phrase_document_frequency: int,
    unigram_document_frequency: int,
) -> bool:
    if phrase.status != "accepted" or unigram.status != "accepted":
        return False
    if phrase.ngram_size < 2 or unigram.ngram_size != 1:
        return False
    if unigram.normalized_term not in term_tokens(phrase.normalized_term):
        return False
    coverage = phrase_document_frequency / max(1, unigram_document_frequency)
    return phrase.score >= unigram.score * 1.15 and coverage >= 0.50


def _invalid_token(token: str) -> bool:
    if len(token) > 64 or _IDENTIFIER_FRAGMENT.fullmatch(token):
        return True
    alphanumeric = sum(character.isalnum() for character in token)
    digits = sum(character.isdigit() for character in token)
    if not alphanumeric:
        return True
    return digits / alphanumeric > 0.5
