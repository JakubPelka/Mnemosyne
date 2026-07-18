import time
import threading
import sqlite3

from scripts.semantic_tagger.job_store import JobStore, JobExecutionContext, LeaseLostError
from scripts.semantic_tagger.ollama_client import OllamaClient
from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit


class HeartbeatThread(threading.Thread):
    def __init__(self, store: JobStore, run_id: str, worker_id: str):
        super().__init__(daemon=True)
        self.store = store
        self.run_id = run_id
        self.worker_id = worker_id
        self.running = True
        self.active_context = None
        self._lock = threading.Lock()

    def set_active_job(self, context: JobExecutionContext):
        with self._lock:
            self.active_context = context

    def clear_active_job(self):
        with self._lock:
            self.active_context = None

    def run(self):
        import contextlib

        while self.running:
            import datetime

            now = datetime.datetime.utcnow().isoformat()

            try:
                with contextlib.closing(sqlite3.connect(self.store.db_path, timeout=5.0)) as conn:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA busy_timeout = 5000")
                    with conn:
                        conn.execute(
                            "UPDATE worker_state SET heartbeat_at = ? WHERE run_id = ? AND worker_id = ?",
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

            time.sleep(3)

    def stop(self):
        self.running = False
        self.join()


def run_worker_loop(
    model_name: str,
    target_run_id: str = None,
    max_claims: int = 0,
    target_done: int = 0,
    schema_version="semantic-tags-v2",
    strategy_version="unit-v1",
    store: JobStore = None,
):
    if not store:
        store = JobStore()

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

        # Verify model config matches the run config
        if run_row["model_name"] != model_name:
            print(
                f"Model mismatch. Run requires {run_row['model_name']}, but worker provided {model_name}"
            )
            return

        # Check for other active workers atomically using a transaction
        conn.execute("BEGIN EXCLUSIVE")
        try:
            active_workers = conn.execute(
                "SELECT worker_id FROM worker_state WHERE run_id = ? AND status = 'running' AND datetime(heartbeat_at) > datetime('now', '-30 seconds')",
                (run_id,),
            ).fetchall()

            if active_workers:
                print("Another live worker already owns this run.")
                conn.execute("ROLLBACK")
                return

            worker_id = "w1"
            import socket

            hostname = socket.gethostname()
            import os

            pid = os.getpid()

            import datetime

            now = datetime.datetime.utcnow().isoformat()

            conn.execute(
                "INSERT OR REPLACE INTO worker_state (run_id, worker_id, worker_pid, hostname, status, started_at, heartbeat_at, pause_requested, stop_after_current_requested) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
                (run_id, worker_id, pid, hostname, "running", now, now),
            )
            conn.execute("COMMIT")
        except Exception as e:
            conn.execute("ROLLBACK")
            print(f"Failed to acquire worker lock: {e}")
            return

    heartbeat = HeartbeatThread(store, run_id, worker_id)
    heartbeat.start()

    client = OllamaClient(model_name)
    worker = Worker(store, client)

    jobs_claimed_this_session = 0
    stop_signal_received = False

    try:
        while True:
            if stop_signal_received:
                break

            with sqlite3.connect(store.db_path) as conn:
                w_state = conn.execute(
                    "SELECT pause_requested, stop_after_current_requested FROM worker_state WHERE run_id = ? AND worker_id = ?",
                    (target_run_id, worker_id),
                ).fetchone()
                if w_state:
                    if w_state[0]:
                        heartbeat.running = False
                        print("Pause requested. Exiting.")
                        break
                    if w_state[1]:
                        print("Stop after current requested. Will exit after this job.")
                        stop_signal_received = True

                if target_done > 0:
                    done_count = conn.execute(
                        "SELECT COUNT(*) FROM tagging_job WHERE run_id = ? AND status = 'done'",
                        (target_run_id,),
                    ).fetchone()[0]
                    if done_count >= target_done:
                        print(f"Target done count ({target_done}) reached. Exiting.")
                        break

            if max_claims > 0 and jobs_claimed_this_session >= max_claims:
                print(f"Max claims limit ({max_claims}) reached. Exiting worker loop.")
                break

            # Claim job
            job = store.claim_next_job(target_run_id, worker_id)
            if not job:
                # Sleep and poll again
                import time

                time.sleep(2)
                continue

            jobs_claimed_this_session += 1
            print(f"Processing job {job['job_id']}...")

            with sqlite3.connect(store.db_path) as conn:
                import datetime

                now = datetime.datetime.utcnow().isoformat()
                conn.execute(
                    "UPDATE worker_state SET last_job_started_at = ? WHERE run_id = ? AND worker_id = ?",
                    (now, run_id, worker_id),
                )

            # Reconstruct Unit
            try:
                unit = load_and_reconstruct_unit(
                    job["unit_id"],
                    str(store.db_path),
                    run_row["schema_version"],
                    run_row["unit_strategy_version"],
                )

                # Double check content hash (load_and_reconstruct_unit already throws if mismatch)
                ctx = JobExecutionContext(
                    job_id=job["job_id"],
                    attempt_id=job["attempt_id"],
                    lease_token=job["lease_token"],
                )
                heartbeat.set_active_job(ctx)

                success = worker.run_one(job, unit, ctx)
                heartbeat.clear_active_job()
                if success:
                    print(f"Job {job['job_id']} completed successfully.")
                else:
                    print(f"Job {job['job_id']} failed logic.")
            except ValueError as e:
                print(f"Job {job['job_id']} failed reconstruction: {e}")
                store.fail_job(
                    job["job_id"],
                    job["attempt_id"],
                    job["lease_token"],
                    "unit_content_hash_mismatch",
                    str(e),
                )
            except Exception as e:
                print(f"Job {job['job_id']} failed unexpected: {e}")
                store.fail_job(
                    job["job_id"], job["attempt_id"], job["lease_token"], "unexpected_error", str(e)
                )

            with sqlite3.connect(store.db_path) as conn:
                import datetime

                now = datetime.datetime.utcnow().isoformat()
                conn.execute(
                    "UPDATE worker_state SET last_job_completed_at = ? WHERE run_id = ? AND worker_id = ?",
                    (now, run_id, worker_id),
                )

    finally:
        heartbeat.stop()
        heartbeat.join()
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE worker_state SET status = 'completed' WHERE run_id = ? AND worker_id = ?",
                (run_id, worker_id),
            )
