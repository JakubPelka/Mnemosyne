import time
import threading
import socket
import sqlite3
from pathlib import Path

from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.ollama_client import OllamaClient
from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit


class HeartbeatThread(threading.Thread):
    def __init__(self, db_path: Path, run_id: str, worker_id: str):
        super().__init__(daemon=True)
        self.db_path = db_path
        self.run_id = run_id
        self.worker_id = worker_id
        self.running = True

    def run(self):
        # Własne połączenie SQLite dla wątku
        with sqlite3.connect(self.db_path) as conn:
            while self.running:
                import datetime

                now = datetime.datetime.utcnow().isoformat()
                try:
                    conn.execute(
                        "UPDATE worker_state SET heartbeat_at = ? WHERE run_id = ? AND worker_id = ?",
                        (now, self.run_id, self.worker_id),
                    )
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
                for _ in range(10):
                    if not self.running:
                        break
                    time.sleep(0.5)

    def stop(self):
        self.running = False


def run_worker_loop(model_name: str, schema_version="semantic-tags-v1", strategy_version="unit-v1"):
    store = JobStore()

    # Get active run
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        run_row = conn.execute(
            "SELECT run_id FROM tagging_run ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not run_row:
            print("No active tagging run found.")
            return

        run_id = run_row["run_id"]

        # Check for other active workers
        active_workers = conn.execute(
            "SELECT worker_id FROM worker_state WHERE run_id = ? AND status = 'running' AND datetime(heartbeat_at) > datetime('now', '-30 seconds')",
            (run_id,),
        ).fetchall()

        if active_workers:
            print("Another worker is already running for this run.")
            return

    worker_id = "w1"
    hostname = socket.gethostname()
    import os

    pid = os.getpid()

    import datetime

    now = datetime.datetime.utcnow().isoformat()

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO worker_state (run_id, worker_id, worker_pid, hostname, status, started_at, heartbeat_at, pause_requested, stop_after_current_requested) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (run_id, worker_id, pid, hostname, "running", now, now),
        )

    heartbeat = HeartbeatThread(store.db_path, run_id, worker_id)
    heartbeat.start()

    client = OllamaClient(model_name)
    worker = Worker(store, client)

    try:
        while True:
            # Check worker state
            with sqlite3.connect(store.db_path) as conn:
                conn.row_factory = sqlite3.Row
                state = conn.execute(
                    "SELECT pause_requested, stop_after_current_requested FROM worker_state WHERE run_id = ? AND worker_id = ?",
                    (run_id, worker_id),
                ).fetchone()

                if state:
                    if state["stop_after_current_requested"]:
                        print("Stop requested. Exiting worker loop.")
                        break
                    if state["pause_requested"]:
                        time.sleep(2)
                        continue

            job = store.claim_next_job(worker_id)
            if not job:
                print("No more pending jobs. Exiting worker loop.")
                break

            print(f"Processing job {job['job_id']}...")

            with sqlite3.connect(store.db_path) as conn:
                now = datetime.datetime.utcnow().isoformat()
                conn.execute(
                    "UPDATE worker_state SET last_job_started_at = ? WHERE run_id = ? AND worker_id = ?",
                    (now, run_id, worker_id),
                )

            # Reconstruct Unit
            try:
                unit = load_and_reconstruct_unit(
                    job["unit_id"], str(store.db_path), schema_version, strategy_version
                )

                # Double check content hash (load_and_reconstruct_unit already throws if mismatch)
                success = worker.run_one(job, unit)
                if success:
                    print(f"Job {job['job_id']} completed successfully.")
                else:
                    print(f"Job {job['job_id']} failed logic.")
            except ValueError as e:
                print(f"Job {job['job_id']} failed reconstruction: {e}")
                store.fail_job(job["job_id"], "unit_content_hash_mismatch", str(e))
            except Exception as e:
                print(f"Job {job['job_id']} failed unexpected: {e}")
                store.fail_job(job["job_id"], "unexpected_error", str(e))

            with sqlite3.connect(store.db_path) as conn:
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
