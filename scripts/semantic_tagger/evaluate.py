import sqlite3
import yaml
from pathlib import Path
from scripts.semantic_tagger.consolidate import consolidate_conversation


def export_review(
    db_path: Path,
    output_yaml_path: Path,
    status: str = "review",
    audit_db_sha256: str = None,
    audit_db_snapshot_date: str = None,
):
    # Verify SHA-256
    import hashlib

    with open(db_path, "rb") as f:
        file_sha256 = hashlib.sha256(f.read()).hexdigest()

    if audit_db_sha256 and file_sha256 != audit_db_sha256:
        raise ValueError(
            f"Database SHA-256 mismatch. Expected {audit_db_sha256}, got {file_sha256}"
        )

    db_uri = f"file:{db_path.absolute()}?mode=ro"
    with sqlite3.connect(db_uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        contexts = conn.execute(
            "SELECT DISTINCT u.context_id FROM tagging_job j JOIN tagging_unit u ON j.unit_id = u.unit_id WHERE j.status = 'done'"
        ).fetchall()

    review_data = []

    for row in contexts:
        ctx_id = row["context_id"]
        consolidated = consolidate_conversation(ctx_id, str(db_path))

        if not consolidated.primary_concepts and not consolidated.secondary_concepts:
            continue
        with sqlite3.connect(db_uri, uri=True) as conn:
            units_count = conn.execute(
                "SELECT COUNT(DISTINCT unit_id) FROM tagging_unit WHERE context_id = ?", (ctx_id,)
            ).fetchone()[0]

        entry = {
            "context_id": ctx_id,
            "units_count": units_count,
            "content_types": consolidated.content_types,
            "primary_concepts": [
                {
                    "label": c.canonical_suggestion,
                    "type": c.concept_type,
                    "confidence": round(c.confidence, 2),
                }
                for c in consolidated.primary_concepts
            ],
            "secondary_concepts": [
                {
                    "label": c.canonical_suggestion,
                    "type": c.concept_type,
                    "confidence": round(c.confidence, 2),
                }
                for c in consolidated.secondary_concepts
            ],
            "project_candidates": [
                {"label": c.canonical_suggestion, "confidence": round(c.confidence, 2)}
                for c in consolidated.project_candidates
            ],
            "source": "hybrid",
            "review": {
                "rating": "accept | wrong | too_generic | too_specific | duplicate | missing | wrong_type",
                "expected_missing": [],
            },
        }
        review_data.append(entry)

    final_data = {
        "status": status,
        "audit_integrity": "compromised_by_manual_sidecar_updates"
        if status == "exploratory"
        else "verified",
        "source_sha256": file_sha256,
        "reviews": review_data,
    }

    with open(output_yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(final_data, f, allow_unicode=True, sort_keys=False)
