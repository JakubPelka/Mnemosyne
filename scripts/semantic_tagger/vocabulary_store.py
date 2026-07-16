import sqlite3
import datetime
from pathlib import Path
import uuid
import unicodedata


def normalize_label(label: str) -> str:
    # Trim, Unicode normalization, lowercasing
    n = unicodedata.normalize("NFKC", label).strip().lower()
    return n


class VocabularyStore:
    def __init__(self, db_path: Path = Path("data/semantic_vocabulary.local.sqlite3")):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vocabulary_candidate (
                    candidate_id TEXT PRIMARY KEY,
                    dimension TEXT NOT NULL,
                    normalized_label TEXT NOT NULL,
                    preferred_label TEXT NOT NULL,
                    language TEXT NOT NULL,
                    status TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    max_confidence REAL NOT NULL DEFAULT 0.0,
                    created_by TEXT NOT NULL,
                    UNIQUE(dimension, normalized_label, language)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vocabulary_label (
                    candidate_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    language TEXT NOT NULL,
                    label_kind TEXT NOT NULL,
                    UNIQUE(candidate_id, label, language, label_kind)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vocabulary_mapping (
                    candidate_id TEXT NOT NULL,
                    scheme TEXT NOT NULL,
                    external_id TEXT,
                    external_uri TEXT,
                    mapping_relation TEXT,
                    verification_status TEXT,
                    confidence REAL
                )
                """
            )

    def upsert_concept(
        self, dimension: str, original_label: str, confidence: float, language: str = "sv"
    ):
        normalized = normalize_label(original_label)
        now = datetime.datetime.now(datetime.UTC).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Check if candidate exists
            cursor = conn.execute(
                "SELECT candidate_id, max_confidence FROM vocabulary_candidate WHERE dimension = ? AND normalized_label = ? AND language = ?",
                (dimension, normalized, language),
            )
            row = cursor.fetchone()

            if row:
                candidate_id = row[0]
                new_max_confidence = max(row[1], confidence)
                conn.execute(
                    """
                    UPDATE vocabulary_candidate 
                    SET occurrence_count = occurrence_count + 1,
                        last_seen_at = ?,
                        max_confidence = ?
                    WHERE candidate_id = ?
                    """,
                    (now, new_max_confidence, candidate_id),
                )
            else:
                candidate_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO vocabulary_candidate 
                    (candidate_id, dimension, normalized_label, preferred_label, language, status, first_seen_at, last_seen_at, occurrence_count, max_confidence, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        dimension,
                        normalized,
                        original_label,
                        language,
                        "local_candidate",
                        now,
                        now,
                        1,
                        confidence,
                        "llm",
                    ),
                )
                # Insert preferred label
                conn.execute(
                    "INSERT OR IGNORE INTO vocabulary_label (candidate_id, label, language, label_kind) VALUES (?, ?, ?, ?)",
                    (candidate_id, original_label, language, "preferred"),
                )

            # Always insert original surface label if it's new
            conn.execute(
                "INSERT OR IGNORE INTO vocabulary_label (candidate_id, label, language, label_kind) VALUES (?, ?, ?, ?)",
                (candidate_id, original_label, language, "original_surface"),
            )
