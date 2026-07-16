import sqlite3
import unicodedata
from collections import defaultdict
from scripts.semantic_tagger.schemas import (
    TaggerOutput,
    ConsolidatedConcept,
    ProjectCandidate,
    ConversationConsolidationOutput,
)


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = str(text).casefold().strip()
    text = " ".join(text.split())
    text = unicodedata.normalize("NFC", text)
    return text


def consolidate_conversation(context_id: str, db_path: str) -> ConversationConsolidationOutput:
    # 1. Fetch all done jobs for this context
    if not db_path.startswith("file:"):
        import pathlib

        db_path = f"file:{pathlib.Path(db_path).absolute()}?mode=ro"
    with sqlite3.connect(db_path, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT j.output_json, j.unit_id 
            FROM tagging_job j
            JOIN tagging_unit u ON j.unit_id = u.unit_id
            WHERE u.context_id = ? AND j.status = 'done'
            """,
            (context_id,),
        ).fetchall()

    if not rows:
        return ConversationConsolidationOutput(
            primary_concepts=[],
            secondary_concepts=[],
            content_types=[],
            conversation_languages=[],
            project_candidates=[],
        )

    all_langs = set()
    all_content_types = set()

    # normalized_name -> { 'type': str, 'labels': set, 'confidence': max, 'unit_ids': set, 'importance_scores': list }
    concept_map = defaultdict(
        lambda: {
            "type": None,
            "labels": set(),
            "confidence": 0.0,
            "unit_ids": set(),
            "importance_scores": [],
        }
    )

    for r in rows:
        unit_id = r["unit_id"]
        try:
            output = TaggerOutput.model_validate_json(r["output_json"])
        except Exception:
            continue

        all_langs.update(output.languages)
        all_content_types.update(output.content_types)

        for c in output.concepts:
            norm = _normalize(c.label)
            if not norm:
                continue

            cmap = concept_map[norm]
            cmap["type"] = c.concept_type  # last one wins or we can vote
            cmap["labels"].add(c.label)
            cmap["confidence"] = max(cmap["confidence"], c.confidence)
            cmap["unit_ids"].add(unit_id)
            cmap["importance_scores"].append(
                3 if c.importance == "primary" else (2 if c.importance == "secondary" else 1)
            )

    primary = []
    secondary = []
    projects = []

    for norm, cmap in concept_map.items():
        avg_score = sum(cmap["importance_scores"]) / len(cmap["importance_scores"])

        # Pick best label
        sorted_labels = sorted(list(cmap["labels"]), key=lambda x: len(x))
        canonical = sorted_labels[0] if sorted_labels else norm

        cc = ConsolidatedConcept(
            canonical_suggestion=canonical,
            concept_type=cmap["type"],
            confidence=cmap["confidence"],
            source_labels=list(cmap["labels"]),
            source_unit_ids=list(cmap["unit_ids"]),
        )

        if avg_score >= 2.5:
            primary.append(cc)
        else:
            secondary.append(cc)

        if cmap["type"] == "project" and cmap["confidence"] > 0.5:
            projects.append(
                ProjectCandidate(
                    canonical_suggestion=canonical,
                    confidence=cmap["confidence"],
                    source_unit_ids=list(cmap["unit_ids"]),
                )
            )

    # Sort by confidence descending
    primary.sort(key=lambda x: x.confidence, reverse=True)
    secondary.sort(key=lambda x: x.confidence, reverse=True)
    projects.sort(key=lambda x: x.confidence, reverse=True)

    return ConversationConsolidationOutput(
        primary_concepts=primary,
        secondary_concepts=secondary,
        content_types=list(all_content_types),
        conversation_languages=list(all_langs),
        project_candidates=projects,
    )
