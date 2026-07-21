import sqlite3
import datetime
from pathlib import Path
import uuid
import unicodedata

from scripts.semantic_tagger.schemas import validate_concept_label


V3_CONCEPT_LABEL_DIMENSIONS = frozenset({"entity_type", "domain", "context_role"})


def validate_vocabulary_label(dimension: str, label: str) -> str:
    """Apply the shared concept-label contract to v3 concept dimensions."""
    if dimension in V3_CONCEPT_LABEL_DIMENSIONS:
        return validate_concept_label(label)
    return label


def normalize_label(label: str) -> str:
    # Trim, Unicode normalization, lowercasing
    n = unicodedata.normalize("NFKC", label).strip().lower()
    return n


class VocabularyStore:
    def _connect(self, isolation_level=None):
        import sqlite3

        conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=isolation_level)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def __init__(self, db_path: Path = Path("data/semantic_vocabulary.local.sqlite3")):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vocabulary_occurrence (
                    occurrence_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    concept_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(candidate_id, run_id, unit_id, concept_id, dimension)
                )
                """
            )

    def upsert_concept(
        self, dimension: str, original_label: str, confidence: float, language: str = "sv"
    ):
        normalized = normalize_label(original_label)
        now = datetime.datetime.now(datetime.UTC).isoformat()

        with self._connect() as conn:
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

    def upsert_occurrence_v3(
        self,
        dimension: str,
        original_label: str,
        language: str,
        confidence: float,
        run_id: str,
        job_id: str,
        unit_id: str,
        concept_id: str,
    ):
        validate_vocabulary_label(dimension, original_label)
        normalized = normalize_label(original_label)
        now = datetime.datetime.now(datetime.UTC).isoformat()

        with self._connect(isolation_level="IMMEDIATE") as conn:
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
                    "UPDATE vocabulary_candidate SET last_seen_at = ?, max_confidence = ? WHERE candidate_id = ?",
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
                        0,  # Legacy occurrence_count kept intact, we will compute dynamically or update it via triggers
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

            # Record occurrence idempotently
            try:
                occurrence_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO vocabulary_occurrence
                    (occurrence_id, candidate_id, dimension, run_id, job_id, unit_id, concept_id, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        occurrence_id,
                        candidate_id,
                        dimension,
                        run_id,
                        job_id,
                        unit_id,
                        concept_id,
                        confidence,
                        now,
                    ),
                )

                # Update occurrence_count just to be somewhat backwards-compatible
                conn.execute(
                    "UPDATE vocabulary_candidate SET occurrence_count = (SELECT COUNT(*) FROM vocabulary_occurrence WHERE candidate_id = ?) WHERE candidate_id = ?",
                    (candidate_id, candidate_id),
                )
            except sqlite3.IntegrityError:
                # Already recorded this occurrence
                pass

    def update_from_tagger_output_v3(self, output, unit_id: str, run_id: str, job_id: str):
        # concepts
        for c in output.concepts:
            # entities, domains, context_roles
            for e_type in c.entity_types:
                self.upsert_occurrence_v3(
                    "entity_type", e_type, "en", c.confidence, run_id, job_id, unit_id, c.concept_id
                )
            for d in c.domains:
                self.upsert_occurrence_v3(
                    "domain", d, "en", c.confidence, run_id, job_id, unit_id, c.concept_id
                )
            for role in c.context_roles:
                self.upsert_occurrence_v3(
                    "context_role", role, "en", c.confidence, run_id, job_id, unit_id, c.concept_id
                )

        # relations
        for r in output.relations:
            # Stable relation ID based on endpoints and predicate and evidence
            import hashlib
            import json

            ev_sorted = sorted(r.evidence_event_ids)
            sig = f"{unit_id}:{r.subject_concept_id}:{r.predicate}:{r.object_concept_id}:{json.dumps(ev_sorted)}"
            rel_id = hashlib.sha256(sig.encode()).hexdigest()
            self.upsert_occurrence_v3(
                "relation_predicate",
                r.predicate,
                "en",
                r.confidence,
                run_id,
                job_id,
                unit_id,
                rel_id,
            )
