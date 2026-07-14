from backend.app.nlp.quality import assess_term, is_export_artifact, phrase_suppresses_unigram
from backend.app.services.topic_overrides import load_topic_overrides
from backend.app.services.topics import stable_term_id, stable_topic_id


def quality(value: str, *, frequency: int = 5, documents: int = 100, tfidf: float = 2.0):
    return assess_term(
        value,
        document_frequency=frequency,
        document_count=documents,
        tfidf_score=tfidf,
    )


def test_rejects_polish_swedish_and_english_stopwords() -> None:
    for value, language in (("teraz", "pl"), ("vill", "sv"), ("more", "en")):
        result = quality(value)
        assert result.status == "rejected"
        assert result.rejection_reason == "stopword"
        assert result.language == language


def test_detects_export_artifacts_and_variants() -> None:
    for value in ("turn search", "cite turn", "turn12search", "filecite3"):
        result = quality(value)
        assert result.rejection_reason == "export_artifact"
        assert is_export_artifact(tuple(value.split())) or value in {"turn12search", "filecite3"}


def test_rejects_short_common_and_invalid_terms() -> None:
    assert quality("xy").rejection_reason == "too_short"
    assert quality("generic", frequency=20).rejection_reason == "too_common"
    assert quality("deadbeefcafebabe").rejection_reason == "invalid_token"
    assert quality("project_123_identifier").rejection_reason == "invalid_token"


def test_rejects_single_occurrence_as_low_information() -> None:
    assert quality("rare-domain", frequency=1).rejection_reason == "low_information"


def test_prefers_an_informative_bigram_over_covered_unigram() -> None:
    phrase = quality("topic graph", frequency=8, tfidf=4.0)
    unigram = quality("graph", frequency=10, tfidf=2.0)
    assert phrase_suppresses_unigram(
        phrase,
        unigram,
        phrase_document_frequency=8,
        unigram_document_frequency=10,
    )


def test_keeps_characteristic_unigram_when_phrase_has_low_coverage() -> None:
    phrase = quality("local atlas", frequency=3, tfidf=4.0)
    unigram = quality("atlas", frequency=20, tfidf=2.0)
    assert not phrase_suppresses_unigram(
        phrase,
        unigram,
        phrase_document_frequency=3,
        unigram_document_frequency=20,
    )


def test_stable_term_and_topic_identifiers() -> None:
    assert stable_term_id("Example Phrase") == stable_term_id("  example   phrase ")
    assert stable_topic_id("example phrase") == stable_topic_id("example phrase")
    assert stable_topic_id("example phrase", manual_id="example") != stable_topic_id(
        "example phrase"
    )


def test_loads_safe_manual_alias_configuration(tmp_path) -> None:
    path = tmp_path / "overrides.yaml"
    path.write_text(
        """
topics:
  - id: example_topic
    name: Example Topic
    aliases: [example, example phrase]
    category: example
""".strip(),
        encoding="utf-8",
    )
    overrides = load_topic_overrides(path)
    assert len(overrides) == 1
    assert overrides[0].override_id == "example_topic"
    assert overrides[0].aliases == ("example", "example phrase")
