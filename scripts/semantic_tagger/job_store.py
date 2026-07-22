import sqlite3
import json
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List


class LeaseLostError(RuntimeError):
    pass


class JobExecutionContext:
    def __init__(self, job_id: str, attempt_id: str, lease_token: str):
        self.job_id = job_id
        self.attempt_id = attempt_id
        self.lease_token = lease_token
        self.lease_lost_event = threading.Event()


DB_PATH = Path("data/semantic_tagger.local.sqlite3")
SIDECAR_DB_SCHEMA_VERSION = 4
MAX_STORED_RESPONSE_BYTES = 1_048_576

SCHEMA = """
CREATE TABLE IF NOT EXISTS sidecar_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO sidecar_meta (key, value) VALUES ('schema_version', '4');

CREATE TABLE IF NOT EXISTS tagging_run (
    run_id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    status TEXT,
    source_database_fingerprint TEXT,
    model_name TEXT,
    model_digest TEXT,
    ollama_version TEXT,
    prompt_version TEXT,
    schema_version TEXT,
    unit_strategy_version TEXT,
    consolidation_prompt_version TEXT,
    settings_json TEXT
);

CREATE TABLE IF NOT EXISTS tagging_unit (
    unit_id TEXT PRIMARY KEY,
    context_id TEXT,
    sequence_no INTEGER,
    content_hash TEXT,
    event_ids_json TEXT,
    segments_json TEXT,
    event_count INTEGER,
    character_count INTEGER,
    estimated_token_count INTEGER,
    first_event_at TEXT,
    last_event_at TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS worker_state (
    run_id TEXT,
    worker_id TEXT,
    worker_pid INTEGER,
    hostname TEXT,
    status TEXT,
    started_at TEXT,
    heartbeat_at TEXT,
    last_job_started_at TEXT,
    last_job_completed_at TEXT,
    pause_requested BOOLEAN DEFAULT 0,
    stop_after_current_requested BOOLEAN DEFAULT 0,
    PRIMARY KEY (run_id, worker_id)
);

CREATE TABLE IF NOT EXISTS tagging_job (
    job_id TEXT PRIMARY KEY,
    job_key TEXT UNIQUE,
    run_id TEXT,
    unit_id TEXT,
    worker_id TEXT,
    status TEXT,
    attempt_count INTEGER DEFAULT 0,
    lease_started_at TEXT,
    lease_expires_at TEXT,
    lease_token TEXT,
    started_at TEXT,
    completed_at TEXT,
    elapsed_ms INTEGER,
    input_hash TEXT,
    output_hash TEXT,
    output_json TEXT,
    error_code TEXT,
    error_summary TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    retry_requested_at TEXT,
    retry_reason TEXT,
    vocabulary_status TEXT DEFAULT 'pending',
    vocabulary_error_code TEXT
);

CREATE TABLE IF NOT EXISTS tagging_attempt (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    worker_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    elapsed_ms INTEGER,
    status TEXT NOT NULL,
    error_code TEXT,
    error_summary TEXT,
    input_hash TEXT,
    output_hash TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    done_reason TEXT,
    request_timeout_seconds INTEGER NOT NULL,
    num_predict INTEGER NOT NULL,
    generation_config_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    response_output_text TEXT,
    response_output_hash TEXT,
    response_output_truncated INTEGER NOT NULL DEFAULT 0,
    response_output_bytes INTEGER,
    response_output_stored_bytes INTEGER,
    UNIQUE(job_id, attempt_no),
    UNIQUE(lease_token)
);

CREATE TABLE IF NOT EXISTS conversation_consolidation (
    consolidation_id TEXT PRIMARY KEY,
    run_id TEXT,
    context_id TEXT,
    job_key TEXT,
    status TEXT,
    attempt_count INTEGER DEFAULT 0,
    input_hash TEXT,
    output_hash TEXT,
    output_json TEXT,
    started_at TEXT,
    completed_at TEXT,
    elapsed_ms INTEGER,
    error_code TEXT
);
"""


class JobStore:
    SIDECAR_DB_SCHEMA_VERSION = 4

    def _connect(self, isolation_level=None):
        import sqlite3

        conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=isolation_level)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db_exists = self.db_path.exists()

        if db_exists:
            # Check version
            import sqlite3

            uri = f"file:{self.db_path.absolute()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                try:
                    cursor = conn.execute(
                        "SELECT value FROM sidecar_meta WHERE key='schema_version'"
                    )
                    row = cursor.fetchone()
                    version = int(row[0]) if row else 1
                except sqlite3.OperationalError:
                    version = 1
            if version != self.SIDECAR_DB_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Sidecar schema mismatch.\nExpected: {self.SIDECAR_DB_SCHEMA_VERSION}\nFound: {version}\nCreate a backup and run an explicit reset command. Old sidecars will not be mutated."
                )

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(SCHEMA)

    def _now(self):
        return datetime.utcnow().isoformat()

    def create_run(self, run_info: Dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        now = self._now()
        settings = run_info.get("settings", {})
        if run_info.get("unit_strategy_version") == "unit-v3-prompt-budgeted-chunks":
            from scripts.semantic_tagger.prompt_budget import (
                PromptBudgetConfig,
                resolve_prompt_estimator_contract,
            )

            settings = dict(settings)
            estimator_version = settings.get(
                "prompt_estimator_version",
                "prompt-estimator-v2-utf8-13-over-40",
            )
            estimator_contract = resolve_prompt_estimator_contract(
                estimator_version,
                run_info.get("model_name") or "",
                persisted_contract=settings.get("prompt_estimator_contract"),
            )
            budget = PromptBudgetConfig(
                num_ctx=settings.get("num_ctx", 8192),
                max_prompt_tokens=settings.get("max_prompt_tokens", 5632),
                num_predict=settings.get("num_predict", 1536),
                safety_margin=settings.get("safety_margin", 1024),
                prompt_estimator_version=estimator_version,
                prompt_estimator_contract=estimator_contract,
                chunk_overlap_characters=settings.get("chunk_overlap_characters", 256),
                chunk_boundary_backtrack_characters=settings.get(
                    "chunk_boundary_backtrack_characters", 256
                ),
            )
            settings.update(budget.as_settings())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tagging_run (run_id, created_at, updated_at, status, source_database_fingerprint, model_name, model_digest, ollama_version, prompt_version, schema_version, unit_strategy_version, consolidation_prompt_version, settings_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    now,
                    now,
                    "created",
                    run_info.get("fingerprint"),
                    run_info.get("model_name"),
                    run_info.get("model_digest"),
                    run_info.get("ollama_version"),
                    run_info.get("prompt_version"),
                    run_info.get("schema_version"),
                    run_info.get("unit_strategy_version"),
                    run_info.get("consolidation_prompt_version"),
                    json.dumps(settings),
                ),
            )
        return run_id

    def save_unit(self, unit: Dict[str, Any], *, require_unique: bool = False):
        insert_verb = "INSERT" if require_unique else "INSERT OR IGNORE"
        with self._connect() as conn:
            conn.execute(
                f"{insert_verb} INTO tagging_unit (unit_id, context_id, sequence_no, content_hash, event_ids_json, segments_json, event_count, character_count, estimated_token_count, first_event_at, last_event_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    unit["unit_id"],
                    unit["context_id"],
                    unit["sequence_no"],
                    unit["content_hash"],
                    json.dumps(unit["event_ids"]),
                    json.dumps(unit["segments"]),
                    unit["event_count"],
                    unit["character_count"],
                    unit["estimated_token_count"],
                    unit["first_event_at"],
                    unit["last_event_at"],
                    self._now(),
                ),
            )

    def queue_job(self, job_key: str, run_id: str, unit_id: str, input_hash: str):
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT unit_strategy_version FROM tagging_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run_row and run_row["unit_strategy_version"] == "unit-v3-prompt-budgeted-chunks":
                raise ValueError(
                    "Prompt-budgeted units require authoritative queue_v3_unit_job validation"
                )
            conn.execute(
                "INSERT OR IGNORE INTO tagging_job (job_id, job_key, run_id, unit_id, status, attempt_count, input_hash) VALUES (?, ?, ?, ?, 'pending', 0, ?)",
                (str(uuid.uuid4()), job_key, run_id, unit_id, input_hash),
            )

    def queue_v3_unit_job(
        self,
        job_key: str,
        run_id: str,
        unit_id: str,
        input_hash: str,
        *,
        main_db_uri: str | None = None,
    ) -> int:
        """Rebuild and remeasure authoritative v3 input immediately before queueing."""
        from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit
        from scripts.semantic_tagger.prompt_budget import (
            PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS,
            PromptBudgetConfig,
            estimate_supported_prompt_variants,
            resolve_prompt_estimator_contract,
        )

        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT unit_strategy_version, prompt_version, schema_version, model_name, "
                "settings_json "
                "FROM tagging_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            unit_row = conn.execute(
                "SELECT content_hash, estimated_token_count, segments_json FROM tagging_unit "
                "WHERE unit_id = ?",
                (unit_id,),
            ).fetchone()
        if not run_row or not unit_row:
            raise ValueError("Cannot queue an unknown prompt-budgeted run or unit")
        if run_row["unit_strategy_version"] != "unit-v3-prompt-budgeted-chunks":
            raise ValueError("queue_v3_unit_job requires the v3 prompt-budget strategy")
        if unit_row["content_hash"] != input_hash:
            raise ValueError("Prompt-budgeted input hash mismatch")

        settings = json.loads(run_row["settings_json"] or "{}")
        estimator_version = settings.get(
            "prompt_estimator_version", "prompt-estimator-v2-utf8-13-over-40"
        )
        estimator_contract = resolve_prompt_estimator_contract(
            estimator_version,
            run_row["model_name"] or "",
            persisted_contract=settings.get("prompt_estimator_contract"),
        )
        budget = PromptBudgetConfig(
            num_ctx=settings.get("num_ctx", 8192),
            max_prompt_tokens=settings.get("max_prompt_tokens", 5632),
            num_predict=settings.get("num_predict", 1536),
            safety_margin=settings.get("safety_margin", 1024),
            prompt_estimator_version=estimator_version,
            prompt_estimator_contract=estimator_contract,
            chunk_overlap_characters=settings.get("chunk_overlap_characters", 256),
            chunk_boundary_backtrack_characters=settings.get(
                "chunk_boundary_backtrack_characters", 256
            ),
        )
        manifest = json.loads(unit_row["segments_json"] or "{}")
        if manifest.get("unit_strategy_version") != run_row["unit_strategy_version"]:
            raise ValueError("Prompt-budgeted unit strategy manifest mismatch")
        if manifest.get("prompt_version") != run_row["prompt_version"]:
            raise ValueError("Prompt-budgeted unit prompt version mismatch")
        if manifest.get("schema_version") != run_row["schema_version"]:
            raise ValueError("Prompt-budgeted unit schema version mismatch")
        if manifest.get("prompt_estimator_version") != budget.prompt_estimator_version:
            raise ValueError("Prompt-budgeted unit estimator manifest mismatch")
        expected_estimator_contract = (
            budget.prompt_estimator_contract.as_manifest()
            if budget.prompt_estimator_contract is not None
            else None
        )
        if manifest.get("prompt_estimator_contract") != expected_estimator_contract:
            raise ValueError("Prompt-budgeted unit tokenizer contract manifest mismatch")
        if manifest.get("prompt_budget_basis") != PROMPT_BUDGET_BASIS_ALL_SUPPORTED_ATTEMPTS:
            raise ValueError("Prompt-budgeted unit was not planned against all supported attempts")

        reconstructed = load_and_reconstruct_unit(
            unit_id,
            str(self.db_path),
            run_row["schema_version"],
            run_row["unit_strategy_version"],
            main_db_uri=main_db_uri,
        )
        assessment = estimate_supported_prompt_variants(
            run_row["prompt_version"],
            reconstructed.content,
            list(reconstructed.event_ids),
            budget.prompt_estimator_version,
            budget.prompt_estimator_contract,
        )
        if manifest.get("prompt_budget") != assessment.as_manifest():
            raise ValueError(
                "Prompt-budgeted manifest estimates do not match authoritative recalculation"
            )
        recalculated = assessment.worst_case_prompt_estimate
        if recalculated != unit_row["estimated_token_count"]:
            raise ValueError(
                "Prompt-budgeted stored worst-case estimate does not match "
                "authoritative recalculation"
            )
        if recalculated > budget.max_prompt_tokens:
            raise ValueError(
                "Refusing to queue a prompt-budgeted unit whose supported prompt variant "
                "exceeds the configured limit"
            )

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tagging_job (job_id, job_key, run_id, unit_id, status, "
                "attempt_count, input_hash) VALUES (?, ?, ?, ?, 'pending', 0, ?)",
                (str(uuid.uuid4()), job_key, run_id, unit_id, input_hash),
            )
        return recalculated

    def claim_next_job(
        self, run_id: str, worker_id: str, lease_seconds: int = 600
    ) -> Dict[str, Any]:
        import hashlib

        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row

            row = conn.execute(
                "SELECT * FROM tagging_job WHERE run_id = ? AND status = 'pending' AND attempt_count < 3 ORDER BY attempt_count ASC LIMIT 1",
                (run_id,),
            ).fetchone()

            if not row:
                return None

            job_id = row["job_id"]

            attempt_row = conn.execute(
                "SELECT MAX(attempt_no) as m FROM tagging_attempt WHERE job_id = ?", (job_id,)
            ).fetchone()
            attempt_no = (attempt_row["m"] or 0) + 1

            attempt_id = str(uuid.uuid4())
            lease_token = str(uuid.uuid4())

            import datetime

            dt_now = datetime.datetime.utcnow()
            expires = dt_now + datetime.timedelta(seconds=lease_seconds)
            now_str = dt_now.isoformat()
            expires_str = expires.isoformat()

            run_row = conn.execute(
                "SELECT * FROM tagging_run WHERE run_id = ?", (run_id,)
            ).fetchone()
            settings = json.loads(run_row["settings_json"]) if run_row else {}
            num_predict = settings.get("num_predict", 2048)
            request_timeout_seconds = settings.get("request_timeout_seconds", 3600)

            num_ctx = settings.get("num_ctx", 8192)

            generation_config_hash = hashlib.sha256(
                json.dumps(
                    {
                        "think": settings.get("think", False),
                        "temperature": settings.get("temperature", 0),
                        "seed": settings.get("seed", 42),
                        "stream": settings.get("stream", False),
                        "num_predict": num_predict,
                        "num_ctx": num_ctx,
                        "request_timeout_seconds": request_timeout_seconds,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()

            conn.execute(
                "INSERT INTO tagging_attempt (attempt_id, job_id, attempt_no, worker_id, lease_token, started_at, status, request_timeout_seconds, num_predict, generation_config_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                (
                    attempt_id,
                    job_id,
                    attempt_no,
                    worker_id,
                    lease_token,
                    now_str,
                    request_timeout_seconds,
                    num_predict,
                    generation_config_hash,
                    now_str,
                ),
            )

            conn.execute(
                "UPDATE tagging_job SET status = 'running', attempt_count = attempt_count + 1, lease_started_at = ?, lease_expires_at = ?, lease_token = ?, worker_id = ?, started_at = COALESCE(started_at, ?) WHERE job_id = ?",
                (now_str, expires_str, lease_token, worker_id, now_str, job_id),
            )

            conn.commit()

            job_dict = dict(
                conn.execute("SELECT * FROM tagging_job WHERE job_id = ?", (job_id,)).fetchone()
            )
            job_dict["attempt_id"] = attempt_id
            job_dict["lease_token"] = lease_token
            job_dict["num_predict"] = num_predict
            job_dict["num_ctx"] = num_ctx
            job_dict["seed"] = settings.get("seed", 42)
            job_dict["settings"] = settings
            job_dict["prompt_version"] = (
                run_row["prompt_version"] if run_row and run_row["prompt_version"] else None
            )
            job_dict["schema_version"] = (
                run_row["schema_version"] if run_row and run_row["schema_version"] else None
            )
            return job_dict

    def renew_lease(self, job_id: str, attempt_id: str, lease_token: str, lease_seconds: int = 900):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            import datetime

            dt_now = datetime.datetime.utcnow()
            expires = dt_now + datetime.timedelta(seconds=lease_seconds)
            cursor = conn.execute(
                "UPDATE tagging_job SET lease_expires_at = ? WHERE job_id = ? AND lease_token = ? AND status = 'running' AND EXISTS (SELECT 1 FROM tagging_attempt WHERE attempt_id = ? AND status = 'running')",
                (expires.isoformat(), job_id, lease_token, attempt_id),
            )
            if cursor.rowcount == 0:
                raise RuntimeError("LeaseLostError: Cannot renew lease")

    def set_run_status(self, run_id: str, new_status: str):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.execute("UPDATE tagging_run SET status = ? WHERE run_id = ?", (new_status, run_id))

    def get_run_status(self, run_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM tagging_run WHERE run_id = ?", (run_id,)
            ).fetchone()
            return row[0] if row else "unknown"

    def recover_expired_leases(self, run_id: str):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            now = self._now()
            # find running jobs with expired lease
            rows = conn.execute(
                "SELECT tj.job_id, ta.attempt_id FROM tagging_job tj JOIN tagging_attempt ta ON tj.job_id = ta.job_id WHERE tj.run_id = ? AND tj.status = 'running' AND tj.lease_expires_at < ? AND ta.status = 'running'",
                (run_id, now),
            ).fetchall()

            for row in rows:
                job_id, attempt_id = row
                conn.execute(
                    "UPDATE tagging_attempt SET status = 'interrupted', error_code = 'stale_lease' WHERE attempt_id = ?",
                    (attempt_id,),
                )
                conn.execute(
                    "UPDATE tagging_job SET status = 'pending', lease_started_at = NULL, lease_expires_at = NULL, lease_token = NULL WHERE job_id = ?",
                    (job_id,),
                )

    def get_pending_jobs(self, run_id: str, limit: int = 10) -> List[Dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            # Restore expired leases

            rows = conn.execute(
                "SELECT * FROM tagging_job WHERE run_id = ? AND status = 'pending' AND attempt_count < 3 ORDER BY attempt_count ASC LIMIT ?",
                (
                    run_id,
                    limit,
                ),
            ).fetchall()
            return [dict(r) for r in rows]

    def lease_job(self, job_id: str, lease_seconds: int = 300) -> bool:
        now = datetime.utcnow()
        expires = datetime.utcfromtimestamp(now.timestamp() + lease_seconds).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tagging_job SET status = 'running', lease_started_at = ?, lease_expires_at = ?, attempt_count = attempt_count + 1 WHERE job_id = ? AND status IN ('pending', 'error')",
                (now.isoformat(), expires, job_id),
            )
            return cursor.rowcount > 0

    def complete_job(
        self,
        job_id: str,
        attempt_id: str,
        lease_token: str,
        output_json: str,
        output_hash: str,
        elapsed_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
    ):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row
            now = self._now()
            row = conn.execute(
                "SELECT * FROM tagging_job tj JOIN tagging_attempt ta ON tj.job_id = ta.job_id WHERE tj.job_id = ? AND ta.attempt_id = ? AND tj.lease_token = ? AND tj.status = 'running' AND ta.status = 'running'",
                (job_id, attempt_id, lease_token),
            ).fetchone()
            if not row:
                raise RuntimeError("Cannot complete job: lease expired or invalid token")

            conn.execute(
                "UPDATE tagging_job SET status = 'done', completed_at = ?, elapsed_ms = ?, output_json = ?, output_hash = ?, prompt_tokens = ?, completion_tokens = ? WHERE job_id = ?",
                (
                    now,
                    elapsed_ms,
                    output_json,
                    output_hash,
                    prompt_tokens,
                    completion_tokens,
                    job_id,
                ),
            )
            conn.execute(
                "UPDATE tagging_attempt SET status = 'done', completed_at = ?, elapsed_ms = ?, output_hash = ?, prompt_tokens = ?, completion_tokens = ? WHERE attempt_id = ?",
                (now, elapsed_ms, output_hash, prompt_tokens, completion_tokens, attempt_id),
            )

    def record_attempt_response_metadata(
        self,
        attempt_id: str,
        job_id: str,
        lease_token: str,
        elapsed_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        done_reason: str,
        raw_response_text: str = None,
    ):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tagging_job WHERE job_id = ? AND status = 'running' AND lease_token = ?",
                (job_id, lease_token),
            ).fetchone()
            if not row:
                raise RuntimeError(
                    f"Cannot record metadata: lease expired or invalid token for job {job_id}"
                )

            import hashlib

            response_output_hash = None
            response_output_truncated = 0
            response_output_bytes = 0
            response_output_stored_bytes = 0
            stored_text = raw_response_text

            if raw_response_text is not None:
                encoded = raw_response_text.encode("utf-8")
                response_output_bytes = len(encoded)
                response_output_hash = hashlib.sha256(encoded).hexdigest()

                if response_output_bytes > MAX_STORED_RESPONSE_BYTES:
                    response_output_truncated = 1
                    stored_text = encoded[:MAX_STORED_RESPONSE_BYTES].decode(
                        "utf-8", errors="ignore"
                    )
                    response_output_stored_bytes = len(stored_text.encode("utf-8"))
                else:
                    response_output_stored_bytes = response_output_bytes

            try:
                conn.execute(
                    "UPDATE tagging_attempt SET elapsed_ms = ?, prompt_tokens = ?, completion_tokens = ?, done_reason = ?, response_output_text = ?, response_output_hash = ?, response_output_truncated = ?, response_output_bytes = ?, response_output_stored_bytes = ? WHERE attempt_id = ?",
                    (
                        elapsed_ms,
                        prompt_tokens,
                        completion_tokens,
                        done_reason,
                        stored_text,
                        response_output_hash,
                        response_output_truncated,
                        response_output_bytes,
                        response_output_stored_bytes,
                        attempt_id,
                    ),
                )
            except sqlite3.OperationalError:
                # Fallback for old v3 sidecars that do not have the new columns
                conn.execute(
                    "UPDATE tagging_attempt SET elapsed_ms = ?, prompt_tokens = ?, completion_tokens = ?, done_reason = ? WHERE attempt_id = ?",
                    (elapsed_ms, prompt_tokens, completion_tokens, done_reason, attempt_id),
                )

    def retry_job(self, job_id: str, reason: str):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row
            now = self._now()
            row = conn.execute("SELECT * FROM tagging_job WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise RuntimeError(f"Job {job_id} not found")
            if row["status"] in ("done", "running"):
                raise RuntimeError(f"Cannot retry job in status {row['status']}")

            conn.execute(
                "UPDATE tagging_job SET status = 'pending', retry_requested_at = ?, retry_reason = ? WHERE job_id = ?",
                (now, reason, job_id),
            )

    def fail_job(
        self,
        job_id: str,
        attempt_id: str,
        lease_token: str,
        error_code: str,
        error_summary: str,
        done_reason: str = None,
        retry_reason: str = None,
    ):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row
            now = self._now()
            row = conn.execute(
                "SELECT * FROM tagging_job WHERE job_id = ? AND status = 'running' AND lease_token = ?",
                (job_id, lease_token),
            ).fetchone()
            if not row:
                raise RuntimeError("Cannot fail job: lease expired or invalid token")

            if retry_reason and row["attempt_count"] < 3:
                conn.execute(
                    "UPDATE tagging_job SET status = 'pending', lease_started_at = NULL, lease_expires_at = NULL, lease_token = NULL, retry_requested_at = ?, retry_reason = ? WHERE job_id = ?",
                    (now, retry_reason, job_id),
                )
            else:
                conn.execute(
                    "UPDATE tagging_job SET status = 'failed', error_code = ?, error_summary = ? WHERE job_id = ?",
                    (error_code, error_summary, job_id),
                )

            conn.execute(
                "UPDATE tagging_attempt SET status = 'failed', completed_at = ?, error_code = ?, error_summary = ?, done_reason = ? WHERE attempt_id = ?",
                (now, error_code, error_summary, done_reason, attempt_id),
            )

    def mark_vocabulary_done(self, job_id: str):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.execute(
                "UPDATE tagging_job SET vocabulary_status = 'done' WHERE job_id = ?", (job_id,)
            )

    def mark_vocabulary_failed(self, job_id: str, error_code: str):
        with self._connect(isolation_level="IMMEDIATE") as conn:
            conn.execute(
                "UPDATE tagging_job SET vocabulary_status = 'failed', vocabulary_error_code = ? WHERE job_id = ?",
                (error_code, job_id),
            )
