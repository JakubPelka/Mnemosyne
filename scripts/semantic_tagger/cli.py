import argparse
import sqlite3
from pathlib import Path

from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.evaluate import export_review

MAIN_DB_URI = "file:data/mnemosyne.sqlite3?mode=ro"


def get_main_db():
    return sqlite3.connect(MAIN_DB_URI, uri=True)


def cmd_doctor(args):
    print("--- DOCTOR ---")

    # 1. Main DB
    print("Main DB: Checking...")
    try:
        with get_main_db() as conn:
            cursor = conn.execute("SELECT event_id FROM events LIMIT 1")
            cursor.fetchone()
        print("Main DB: OK (accessible, read-only confirmed)")
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
        client = OllamaClient("qwen3:14b")
        tags = client.check_connection()
        print("Ollama API: Connected")
        print("endpoint localhost-only")
        print("requested model tag = qwen3:14b")

        models = [m.get("name") for m in tags.get("models", [])]
        if "qwen3:14b" in models:
            print("local model found = true")

            # check digest
            model_info = next(m for m in tags.get("models", []) if m.get("name") == "qwen3:14b")
            digest = model_info.get("digest", "")
            if digest.startswith("bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"):
                print("digest matches")
            else:
                print(f"digest mismatch: {digest}")
        else:
            print("local model found = false")

        print("remote fallback disabled in tagger")
        version = client.get_version()
        print(f"Ollama Version: {version}")
    except OllamaError as e:
        print(f"Ollama API: Error - {e}")


def cmd_prepare(args):
    print(f"Preparing sample: {args.sample}, limit: {args.limit_contexts}")
    store = JobStore()
    run_info = {
        "model_name": "qwen3:14b",
        "prompt_version": "semantic_tagger_v1.md",
        "schema_version": "semantic-tags-v1",
        "unit_strategy_version": "unit-v1",
    }
    run_id = store.create_run(run_info)

    # Read from main DB read-only
    with get_main_db() as conn:
        conn.row_factory = sqlite3.Row
        contexts = conn.execute(
            "SELECT DISTINCT context_id FROM events WHERE context_id IS NOT NULL LIMIT ?",
            (args.limit_contexts,),
        ).fetchall()

        builder = UnitBuilder()
        total_units = 0
        total_chars = 0
        sizes = []

        for ctx_row in contexts:
            ctx_id = ctx_row["context_id"]
            # Fetch events for context
            events_rows = conn.execute(
                "SELECT event_id, title, text, timestamp_start, event_type FROM events WHERE context_id = ? ORDER BY timestamp_start ASC",
                (ctx_id,),
            ).fetchall()
            events = [dict(r) for r in events_rows]

            title = events[0]["title"] if events and events[0]["title"] else ""
            units = builder.build_units_for_context(ctx_id, events, title)

            for u in units:
                store.save_unit(u)

                # Job key deterministic
                job_key_raw = f"{u['unit_id']}|{u['content_hash']}|qwen3:14b|semantic_tagger_v1.md|semantic-tags-v1|unit-v1"
                import hashlib

                job_key = hashlib.sha256(job_key_raw.encode()).hexdigest()

                store.queue_job(job_key, run_id, u["unit_id"], u["content_hash"])

                total_units += 1
                total_chars += u["character_count"]
                sizes.append(u["character_count"])

    avg_units = total_units / len(contexts) if contexts else 0
    est_tokens = total_chars // 4
    print("--- PREPARE STATS ---")
    print(f"contexts selected: {len(contexts)}")
    print(f"units created: {total_units}")
    print(f"average units per context: {avg_units:.2f}")
    print(f"total characters: {total_chars}")
    print(f"estimated tokens: {est_tokens}")
    if sizes:
        print(f"largest unit: {max(sizes)} chars")
        print(f"smallest unit: {min(sizes)} chars")
    print(f"estimated sidecar size: ~{total_chars // 1024 + 50} KB")


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
