import datetime
import json
import os
import signal
import socket
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit
from scripts.semantic_tagger.job_store import JobExecutionContext, JobStore, LeaseLostError
from scripts.semantic_tagger.ollama_client import OllamaClient
from scripts.semantic_tagger.runtime_paths import (
    WorkerRuntimePaths,
    validate_worker_runtime_paths,
)
from scripts.semantic_tagger.worker import Worker


def validate_worker_prompt_estimator_contract(run_row) -> None:
    """Fail closed before worker registration when an exact contract has drifted."""
    if run_row["unit_strategy_version"] != "unit-v3-prompt-budgeted-chunks":
        return
    from scripts.semantic_tagger.prompt_budget import resolve_prompt_estimator_contract

    settings = json.loads(run_row["settings_json"] or "{}")
    resolve_prompt_estimator_contract(
        settings.get(
            "prompt_estimator_version",
            "prompt-estimator-v2-utf8-13-over-40",
        ),
        run_row["model_name"] or "",
        persisted_contract=settings.get("prompt_estimator_contract"),
    )


class HeartbeatThread(threading.Thread):
    def __init__(self, store: JobStore, run_id: str, worker_id: str):
        super().__init__(daemon=True)
        self.store = store
        self.run_id = run_id
        self.worker_id = worker_id
        self.running = True
        self.active_context = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def set_active_job(self, context: JobExecutionContext):
        with self._lock:
            self.active_context = context

    def clear_active_job(self):
        with self._lock:
            self.active_context = None

    def run(self):
        import contextlib

        while self.running and not self._stop_event.is_set():
            now = datetime.datetime.utcnow().isoformat()

            try:
                with contextlib.closing(sqlite3.connect(self.store.db_path, timeout=5.0)) as conn:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA busy_timeout = 5000")
                    with conn:
                        conn.execute(
                            "UPDATE worker_state SET heartbeat_at = ? "
                            "WHERE run_id = ? AND worker_id = ?",
                            (now, self.run_id, self.worker_id),
                        )
            except sqlite3.OperationalError:
                pass

            with self._lock:
                ctx = self.active_context

            if ctx:
                try:
                    self.store.renew_lease(ctx.job_id, ctx.attempt_id, ctx.lease_token)
                except LeaseLostError:
                    ctx.lease_lost_event.set()
                except sqlite3.OperationalError:
                    pass

            self._stop_event.wait(3)

    def stop(self):
        """Stop and join once; repeated cleanup is harmless."""
        self.running = False
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join()


def mark_worker_inactive(store: JobStore, run_id: str, worker_id: str) -> None:
    """Persist the existing terminal worker state idempotently."""
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE worker_state SET status = 'completed', heartbeat_at = ? "
            "WHERE run_id = ? AND worker_id = ?",
            (datetime.datetime.utcnow().isoformat(), run_id, worker_id),
        )


def _validate_bounds(target_done: int, target_terminal: int, max_claims: int) -> None:
    if target_done < 0:
        raise ValueError("target_done must be at least zero")
    if target_terminal < 0:
        raise ValueError("target_terminal must be at least zero")
    if max_claims < 0:
        raise ValueError("max_claims must be at least zero")
    if target_done > 0 and target_terminal > 0:
        raise ValueError("target_done and target_terminal are mutually exclusive")


def _target_reached(
    store: JobStore,
    run_id: str,
    *,
    target_done: int,
    target_terminal: int,
) -> tuple[bool, str | None]:
    if target_done <= 0 and target_terminal <= 0:
        return False, None
    with sqlite3.connect(store.db_path) as conn:
        done, failed = conn.execute(
            "SELECT "
            "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) "
            "FROM tagging_job WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    done = done or 0
    failed = failed or 0
    if target_done > 0 and done >= target_done:
        return True, f"Target done count ({target_done}) reached. Exiting."
    if target_terminal > 0 and done + failed >= target_terminal:
        return True, f"Target terminal count ({target_terminal}) reached. Exiting."
    return False, None


def _control_state(store: JobStore, run_id: str, worker_id: str) -> tuple[bool, bool]:
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT pause_requested, stop_after_current_requested FROM worker_state "
            "WHERE run_id = ? AND worker_id = ?",
            (run_id, worker_id),
        ).fetchone()
    if not row:
        return False, False
    return bool(row[0]), bool(row[1])


def _install_signal_handlers(shutdown_requested: threading.Event):
    if threading.current_thread() is not threading.main_thread():
        return {}

    previous = {}

    def request_shutdown(_signum, _frame):
        shutdown_requested.set()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_shutdown)
    except BaseException:
        _restore_signal_handlers(previous)
        raise
    return previous


def _restore_signal_handlers(previous) -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def run_worker_loop(
    model_name: str,
    target_run_id: str | None = None,
    max_claims: int = 0,
    target_done: int = 0,
    schema_version="semantic-tags-v2",
    strategy_version="unit-v1",
    store: JobStore | None = None,
    *,
    main_db_path: str | Path,
    vocabulary_db_path: str | Path,
    target_terminal: int = 0,
    runtime_paths: WorkerRuntimePaths | None = None,
    ollama_client_factory: Callable[[str], OllamaClient] | None = None,
    shutdown_event: threading.Event | None = None,
):
    """Run one worker with explicit, preflighted runtime database paths."""
    del schema_version, strategy_version
    _validate_bounds(target_done, target_terminal, max_claims)
    if runtime_paths is not None:
        main_db_path = runtime_paths.main_db_path
        vocabulary_db_path = runtime_paths.vocabulary_db_path
    paths = validate_worker_runtime_paths(main_db_path, vocabulary_db_path)

    if store is None:
        raise ValueError("worker sidecar store must be supplied explicitly")

    registered = False
    heartbeat = None
    shutdown_requested = shutdown_event or threading.Event()
    previous_handlers = {}

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        if target_run_id:
            run_row = conn.execute(
                "SELECT * FROM tagging_run WHERE run_id = ?", (target_run_id,)
            ).fetchone()
            if not run_row:
                print(f"Run {target_run_id} not found.")
                return
        else:
            run_row = conn.execute(
                "SELECT * FROM tagging_run ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not run_row:
                print("No active tagging run found.")
                return

        run_id = run_row["run_id"]
        if run_row["model_name"] != model_name:
            print(
                f"Model mismatch. Run requires {run_row['model_name']}, "
                f"but worker provided {model_name}"
            )
            return

        try:
            validate_worker_prompt_estimator_contract(run_row)
        except ValueError as error:
            print(f"Worker prompt-estimator contract validation failed: {error}")
            return

        previous_handlers = _install_signal_handlers(shutdown_requested)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            active_workers = conn.execute(
                "SELECT worker_id FROM worker_state WHERE run_id = ? "
                "AND status = 'running' "
                "AND datetime(heartbeat_at) > datetime('now', '-30 seconds')",
                (run_id,),
            ).fetchall()
            if active_workers:
                print("Another live worker already owns this run.")
                conn.execute("ROLLBACK")
                _restore_signal_handlers(previous_handlers)
                return

            worker_id = "w1"
            now = datetime.datetime.utcnow().isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO worker_state "
                "(run_id, worker_id, worker_pid, hostname, status, started_at, "
                "heartbeat_at, pause_requested, stop_after_current_requested) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
                (
                    run_id,
                    worker_id,
                    os.getpid(),
                    socket.gethostname(),
                    "running",
                    now,
                    now,
                ),
            )
            conn.execute("COMMIT")
            registered = True
        except Exception as error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            _restore_signal_handlers(previous_handlers)
            print(f"Failed to acquire worker lock: {error}")
            return

    try:
        heartbeat = HeartbeatThread(store, run_id, worker_id)
        heartbeat.start()

        client_factory = ollama_client_factory or OllamaClient
        client = client_factory(model_name)
        worker = Worker(store, client, vocabulary_db_path=paths.vocabulary_db_path)
        jobs_claimed_this_session = 0

        while not shutdown_requested.is_set():
            pause_requested, stop_after_current_requested = _control_state(store, run_id, worker_id)
            if pause_requested:
                print("Pause requested. Exiting.")
                break
            if stop_after_current_requested:
                print("Stop after current requested. Exiting before another claim.")
                break

            reached, message = _target_reached(
                store,
                run_id,
                target_done=target_done,
                target_terminal=target_terminal,
            )
            if reached:
                print(message)
                break
            if max_claims > 0 and jobs_claimed_this_session >= max_claims:
                print(f"Max claims limit ({max_claims}) reached. Exiting worker loop.")
                break

            job = store.claim_next_job(run_id, worker_id)
            if not job:
                reached, message = _target_reached(
                    store,
                    run_id,
                    target_done=target_done,
                    target_terminal=target_terminal,
                )
                if reached:
                    print(message)
                    break
                if shutdown_requested.is_set():
                    break
                time.sleep(2)
                continue

            jobs_claimed_this_session += 1
            print(f"Processing job {job['job_id']}...")
            with sqlite3.connect(store.db_path) as conn:
                conn.execute(
                    "UPDATE worker_state SET last_job_started_at = ? "
                    "WHERE run_id = ? AND worker_id = ?",
                    (datetime.datetime.utcnow().isoformat(), run_id, worker_id),
                )

            try:
                unit = load_and_reconstruct_unit(
                    job["unit_id"],
                    str(store.db_path),
                    run_row["schema_version"],
                    run_row["unit_strategy_version"],
                    main_db_uri=paths.main_db_uri,
                )
                context = JobExecutionContext(
                    job_id=job["job_id"],
                    attempt_id=job["attempt_id"],
                    lease_token=job["lease_token"],
                )
                heartbeat.set_active_job(context)
                success = worker.run_one(job, unit, context)
                if success:
                    print(f"Job {job['job_id']} completed successfully.")
                else:
                    print(f"Job {job['job_id']} failed logic.")
            except ValueError as error:
                print(f"Job {job['job_id']} failed reconstruction: {error}")
                store.fail_job(
                    job["job_id"],
                    job["attempt_id"],
                    job["lease_token"],
                    "unit_content_hash_mismatch",
                    str(error),
                )
            except Exception as error:
                print(f"Job {job['job_id']} failed unexpected: {error}")
                store.fail_job(
                    job["job_id"],
                    job["attempt_id"],
                    job["lease_token"],
                    "unexpected_error",
                    str(error),
                )
            finally:
                heartbeat.clear_active_job()

            with sqlite3.connect(store.db_path) as conn:
                conn.execute(
                    "UPDATE worker_state SET last_job_completed_at = ? "
                    "WHERE run_id = ? AND worker_id = ?",
                    (datetime.datetime.utcnow().isoformat(), run_id, worker_id),
                )

            reached, message = _target_reached(
                store,
                run_id,
                target_done=target_done,
                target_terminal=target_terminal,
            )
            if reached:
                print(message)
                break
    finally:
        try:
            if heartbeat is not None:
                heartbeat.stop()
        finally:
            try:
                if registered:
                    mark_worker_inactive(store, run_id, worker_id)
            finally:
                _restore_signal_handlers(previous_handlers)
