import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

DB_PATH = Path("data/semantic_tagger.local.sqlite3")
EXPECTED_SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sidecar_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO sidecar_meta (key, value) VALUES ('schema_version', '3');

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
    retry_reason TEXT
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
    EXPECTED_SCHEMA_VERSION = 3

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db_exists = self.db_path.exists()

        if db_exists:
            # Check version
            with sqlite3.connect(self.db_path) as conn:
                try:
                    cursor = conn.execute(
                        "SELECT value FROM sidecar_meta WHERE key='schema_version'"
                    )
                    row = cursor.fetchone()
                    version = int(row[0]) if row else 1
                except sqlite3.OperationalError:
                    version = 1
            if version != EXPECTED_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Sidecar schema mismatch.\nExpected: {EXPECTED_SCHEMA_VERSION}\nFound: {version}\nCreate a backup and run an explicit reset command."
                )

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    def _now(self):
        return datetime.utcnow().isoformat()

    def create_run(self, run_info: Dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        now = self._now()
        with sqlite3.connect(self.db_path) as conn:
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
                    json.dumps(run_info.get("settings", {})),
                ),
            )
        return run_id

    def save_unit(self, unit: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tagging_unit (unit_id, context_id, sequence_no, content_hash, event_ids_json, segments_json, event_count, character_count, estimated_token_count, first_event_at, last_event_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tagging_job (job_id, job_key, run_id, unit_id, status, attempt_count, input_hash) VALUES (?, ?, ?, ?, 'pending', 0, ?)",
                (str(uuid.uuid4()), job_key, run_id, unit_id, input_hash),
            )

    def claim_next_job(
        self, run_id: str, worker_id: str, lease_seconds: int = 600
    ) -> Dict[str, Any]:
        import hashlib

        with sqlite3.connect(self.db_path, isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row

            now = self._now()
            conn.execute("UPDATE tagging_job SET status = 'failed' WHERE status = 'error'")
            conn.execute(
                "UPDATE tagging_job SET status = 'pending' WHERE status = 'running' AND lease_expires_at < ?",
                (now,),
            )

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
                "SELECT settings_json FROM tagging_run WHERE run_id = ?", (run_id,)
            ).fetchone()
            settings = json.loads(run_row["settings_json"]) if run_row else {}
            num_predict = settings.get("num_predict", 2048)
            request_timeout_seconds = settings.get("request_timeout_seconds", 3600)

            generation_config_hash = hashlib.sha256(
                json.dumps(
                    {
                        "think": settings.get("think", False),
                        "temperature": settings.get("temperature", 0),
                        "seed": settings.get("seed", 42),
                        "stream": settings.get("stream", False),
                        "num_predict": num_predict,
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
            job_dict["seed"] = settings.get("seed", 42)
            return job_dict

    def renew_lease(self, worker_id: str, lease_token: str, lease_seconds: int = 600):
        with sqlite3.connect(self.db_path) as conn:
            import datetime

            dt_now = datetime.datetime.utcnow()
            expires = dt_now + datetime.timedelta(seconds=lease_seconds)
            conn.execute(
                "UPDATE tagging_job SET lease_expires_at = ? WHERE worker_id = ? AND lease_token = ? AND status = 'running'",
                (expires.isoformat(), worker_id, lease_token),
            )

    def get_pending_jobs(self, run_id: str, limit: int = 10) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Restore expired leases
            now = self._now()
            conn.execute("UPDATE tagging_job SET status = 'failed' WHERE status = 'error'")
            conn.execute(
                "UPDATE tagging_job SET status = 'pending' WHERE status = 'running' AND lease_expires_at < ?",
                (now,),
            )

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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path, isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row
            now = self._now()
            row = conn.execute(
                "SELECT * FROM tagging_job WHERE job_id = ? AND status = 'running' AND lease_token = ?",
                (job_id, lease_token),
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

    def retry_job(self, job_id: str, reason: str):
        with sqlite3.connect(self.db_path, isolation_level="IMMEDIATE") as conn:
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
    ):
        with sqlite3.connect(self.db_path, isolation_level="IMMEDIATE") as conn:
            conn.row_factory = sqlite3.Row
            now = self._now()
            row = conn.execute(
                "SELECT * FROM tagging_job WHERE job_id = ? AND status = 'running' AND lease_token = ?",
                (job_id, lease_token),
            ).fetchone()
            if not row:
                raise RuntimeError("Cannot fail job: lease expired or invalid token")

            conn.execute(
                "UPDATE tagging_job SET status = 'failed', error_code = ?, error_summary = ? WHERE job_id = ?",
                (error_code, error_summary, job_id),
            )
            conn.execute(
                "UPDATE tagging_attempt SET status = 'failed', completed_at = ?, error_code = ?, error_summary = ?, done_reason = ? WHERE attempt_id = ?",
                (now, error_code, error_summary, done_reason, attempt_id),
            )
