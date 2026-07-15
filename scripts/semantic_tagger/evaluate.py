import sqlite3
import yaml
from collections import defaultdict
from pathlib import Path
from scripts.semantic_tagger.consolidate import consolidate_conversation

def export_review(db_path: Path, output_yaml_path: Path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        contexts = conn.execute(
            "SELECT DISTINCT u.context_id FROM tagging_job j JOIN tagging_unit u ON j.unit_id = u.unit_id WHERE j.status = 'done'"
        ).fetchall()
        
    review_data = []
    
    for row in contexts:
        ctx_id = row['context_id']
        consolidated = consolidate_conversation(ctx_id, str(db_path))
        
        if not consolidated.primary_concepts and not consolidated.secondary_concepts:
            continue
            
        with sqlite3.connect(db_path) as conn:
            units_count = conn.execute("SELECT COUNT(DISTINCT unit_id) FROM tagging_unit WHERE context_id = ?", (ctx_id,)).fetchone()[0]
            
        entry = {
            "context_id": ctx_id,
            "units_count": units_count,
            "content_types": consolidated.content_types,
            "primary_concepts": [
                {
                    "label": c.canonical_suggestion,
                    "type": c.concept_type,
                    "confidence": round(c.confidence, 2)
                } for c in consolidated.primary_concepts
            ],
            "secondary_concepts": [
                {
                    "label": c.canonical_suggestion,
                    "type": c.concept_type,
                    "confidence": round(c.confidence, 2)
                } for c in consolidated.secondary_concepts
            ],
            "project_candidates": [
                {
                    "label": c.canonical_suggestion,
                    "confidence": round(c.confidence, 2)
                } for c in consolidated.project_candidates
            ],
            "source": "hybrid",
            "review": {
                "rating": "accept | wrong | too_generic | too_specific | duplicate | missing | wrong_type",
                "expected_missing": []
            }
        }
        review_data.append(entry)
        
    with open(output_yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(review_data, f, allow_unicode=True, sort_keys=False)
