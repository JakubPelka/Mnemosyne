import argparse
import sqlite3
from pathlib import Path
from backend.app.database import create_sqlite_engine, session_factory
from backend.app.models import Event
from sqlalchemy import select

from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError
from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.evaluate import export_review


def cmd_doctor(args):
    print("--- DOCTOR ---")

    # 1. Main DB
    print("Main DB: Checking...")
    try:
        engine = create_sqlite_engine(Path("data/mnemosyne.sqlite3"))
        SessionLocal = session_factory(engine)
        with SessionLocal() as db:
            count = db.scalar(select(Event.event_id).limit(1))
        print("Main DB: OK (accessible)")
    except Exception as e:
        print(f"Main DB: Error - {e}")

    # 2. Sidecar DB
    print("Sidecar DB: Checking...")
    try:
        store = JobStore()
        print(f"Sidecar DB: OK ({store.db_path})")
    except Exception as e:
        print(f"Sidecar DB: Error - {e}")

    # 3. Ollama
    print("Ollama: Checking API...")
    try:
        client = OllamaClient("")
        tags = client.check_connection()
        print("Ollama API: Connected")
        version = client.get_version()
        print(f"Ollama Version: {version}")

        models = [m.get("name") for m in tags.get("models", [])]
        print(f"Ollama Models installed: {models}")
    except OllamaError as e:
        print(f"Ollama API: Error - {e}")


def cmd_prepare(args):
    print(f"Preparing sample: {args.sample}, limit: {args.limit_contexts}")
    # To be implemented for full DB pull.
    # Currently just creates the run in sidecar.
    store = JobStore()
    run_info = {
        "model_name": "unknown (prepare)",
        "prompt_version": "semantic_tagger_v1.md",
        "schema_version": "semantic-tags-v1",
        "unit_strategy_version": "unit-v1",
    }
    run_id = store.create_run(run_info)
    print(f"Run {run_id} created in sidecar.")
    print("Note: Parsing from main DB and pushing to sidecar is simulated in this step.")


def cmd_run(args):
    print(f"Starting run with model {args.model}")
    store = JobStore()
    client = OllamaClient(args.model)
    worker = Worker(store, client)

    jobs = store.get_pending_jobs(limit=args.max_jobs)
    print(f"Found {len(jobs)} pending jobs.")

    for job in jobs:
        print(f"Processing job {job['job_id']}...")
        # Normally content would be retrieved dynamically from main DB using context_id and event_ids.
        # For prototype, simulated processing.
        success = worker.run_one(job, "simulated content", False, False, False)
        if success:
            print(f"Job {job['job_id']} completed successfully.")
        else:
            print(f"Job {job['job_id']} failed.")


def cmd_status(args):
    store = JobStore()
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        stats = conn.execute(
            "SELECT status, count(*) as count FROM tagging_job GROUP BY status"
        ).fetchall()
        print("Job Status:")
        for r in stats:
            print(f"  {r['status']}: {r['count']}")


def cmd_consolidate(args):
    print("Consolidation would happen here, writing to conversation_consolidation table.")


def cmd_export_review(args):
    print(f"Exporting review to {args.output}")
    export_review(Path("data/semantic_tagger.local.sqlite3"), Path(args.output))
    print("Export complete.")


def main():
    parser = argparse.ArgumentParser(description="Semantic Tagger CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")

    parser_prep = subparsers.add_parser("prepare")
    parser_prep.add_argument("--sample", required=True)
    parser_prep.add_argument("--limit-contexts", type=int, default=12)

    parser_run = subparsers.add_parser("run")
    parser_run.add_argument("--model", required=True)
    parser_run.add_argument("--max-jobs", type=int, default=50)

    subparsers.add_parser("status")

    parser_retry = subparsers.add_parser("retry-errors")
    parser_retry.add_argument("--max-attempts", type=int, default=3)

    subparsers.add_parser("consolidate")

    parser_export = subparsers.add_parser("export-review")
    parser_export.add_argument("--output", required=True)

    args = parser.parse_args()

    if args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "prepare":
        cmd_prepare(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "consolidate":
        cmd_consolidate(args)
    elif args.command == "export-review":
        cmd_export_review(args)


if __name__ == "__main__":
    main()
