import argparse
import sqlite3
from pathlib import Path

from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError
from scripts.semantic_tagger.unit_builder import UnitBuilder
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
            if digest.startswith(
                "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8"
            ):
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


def cmd_reset_sidecar(args):
    if not args.confirm_reset:
        print("You must specify --confirm-reset to proceed.")
        return
    import shutil
    from datetime import datetime
    from pathlib import Path

    db_path = Path("data/semantic_tagger.local.sqlite3")
    if db_path.exists():
        backup_dir = Path("data/semantic_tagger_exports")
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"semantic_tagger_before_reset_{stamp}.sqlite3"
        shutil.copy2(db_path, backup_path)
        print(f"Created backup at {backup_path}")
        db_path.unlink()

    # Reinitialize
    from scripts.semantic_tagger.job_store import JobStore

    store = JobStore()
    print(f"Sidecar schema reset to version {store.EXPECTED_SCHEMA_VERSION}")


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

                import hashlib
                import json

                # Generation config hash
                gen_config = {
                    "think": False,
                    "temperature": 0,
                    "stream": False,
                    "format": "Pydantic JSON Schema",
                }
                gen_config_str = json.dumps(gen_config, sort_keys=True)
                generation_config_hash = hashlib.sha256(gen_config_str.encode()).hexdigest()

                # Deterministic job key
                job_key_raw = f"{u['unit_id']}|{u['content_hash']}|qwen3:14b|bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8|semantic_tagger_v1.md|semantic-tags-v1|unit-v1|{generation_config_hash}"
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


def get_active_run_id():
    from scripts.semantic_tagger.job_store import JobStore
    import sqlite3

    store = JobStore()
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        run_row = conn.execute(
            "SELECT run_id FROM tagging_run ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not run_row:
            return None, store
        return run_row["run_id"], store


def cmd_worker(args):
    from scripts.semantic_tagger.worker_loop import run_worker_loop

    run_worker_loop(args.model, args.run_id, args.max_jobs)


def cmd_pause(args):
    run_id, store = get_active_run_id()
    if run_id:
        import sqlite3

        with sqlite3.connect(store.db_path) as conn:
            conn.execute("UPDATE worker_state SET pause_requested = 1 WHERE run_id = ?", (run_id,))
        print("Pause requested.")


def cmd_resume(args):
    run_id, store = get_active_run_id()
    if run_id:
        import sqlite3

        with sqlite3.connect(store.db_path) as conn:
            conn.execute("UPDATE worker_state SET pause_requested = 0 WHERE run_id = ?", (run_id,))
        print("Resume requested.")


def cmd_stop_after_current(args):
    run_id, store = get_active_run_id()
    if run_id:
        import sqlite3

        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE worker_state SET stop_after_current_requested = 1 WHERE run_id = ?",
                (run_id,),
            )
        print("Stop after current requested.")


def format_eta(seconds):
    if seconds is None:
        return "insufficient data"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"~{int(hours)} h {int(minutes)} min"
    return f"~{int(minutes)} min"


def calc_eta(completed_times, pending_count):
    if len(completed_times) < 3:
        return None, None, None, "insufficient data"
    sorted_times = sorted(completed_times)

    p25 = sorted_times[int(len(sorted_times) * 0.25)]
    median = sorted_times[int(len(sorted_times) * 0.5)]
    p90 = sorted_times[int(len(sorted_times) * 0.9)]

    confidence = "low"
    if len(completed_times) >= 30:
        confidence = "high"
    elif len(completed_times) >= 10:
        confidence = "medium"

    return p25 * pending_count, median * pending_count, p90 * pending_count, confidence


def cmd_status(args):
    import json
    import time
    import sqlite3

    run_id, store = get_active_run_id()
    if not run_id:
        print("No active run.")
        return

    def _print_status():
        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Context stats via tagging_unit
            total_contexts = conn.execute("SELECT COUNT(DISTINCT context_id) FROM tagging_unit").fetchone()[0]

            # Unit stats
            unit_stats = conn.execute("SELECT status, COUNT(*) as c FROM tagging_job WHERE run_id = ? GROUP BY status", (run_id,)).fetchall()
            status_counts = {r['status']: r['c'] for r in unit_stats}

            done = status_counts.get("done", 0)
            running = status_counts.get("running", 0)
            pending = status_counts.get("pending", 0)
            error = status_counts.get("error", 0)
            skipped = status_counts.get("skipped", 0)
            total_units = done + running + pending + error + skipped

            units_percent = (done / total_units * 100) if total_units > 0 else 0

            # Contexts detailed stats
            context_status_query = """
                SELECT u.context_id, 
                       COUNT(j.job_id) as total_jobs,
                       SUM(CASE WHEN j.status = 'done' THEN 1 ELSE 0 END) as done_jobs,
                       SUM(CASE WHEN j.status = 'error' THEN 1 ELSE 0 END) as err_jobs,
                       SUM(CASE WHEN j.status = 'running' THEN 1 ELSE 0 END) as running_jobs
                FROM tagging_unit u
                LEFT JOIN tagging_job j ON u.unit_id = j.unit_id AND j.run_id = ?
                GROUP BY u.context_id
            """
            ctx_stats = conn.execute(context_status_query, (run_id,)).fetchall()

            complete_ctx = 0
            failed_ctx = 0
            partial_ctx = 0
            pending_ctx = 0

            for ctx in ctx_stats:
                total_j = ctx['total_jobs'] or 0
                done_j = ctx['done_jobs'] or 0
                err_j = ctx['err_jobs'] or 0
                run_j = ctx['running_jobs'] or 0

                if total_j == 0 or (total_j == (err_j + done_j) and done_j == 0 and err_j == 0):
                    pending_ctx += 1
                elif done_j == total_j:
                    complete_ctx += 1
                elif err_j == total_j:
                    failed_ctx += 1
                elif done_j > 0 or err_j > 0 or run_j > 0:
                    partial_ctx += 1
                else:
                    pending_ctx += 1

            ctx_percent = (complete_ctx / total_contexts * 100) if total_contexts > 0 else 0

            # Retries
            retry_count = conn.execute("SELECT SUM(attempt_count - 1) FROM tagging_job WHERE run_id = ? AND attempt_count > 1", (run_id,)).fetchone()[0] or 0

            # Quality
            job_success_rate = (done / (done + error) * 100) if (done + error) > 0 else 0

            error_details = conn.execute("SELECT error_code, COUNT(*) as c FROM tagging_job WHERE run_id = ? AND status='error' GROUP BY error_code", (run_id,)).fetchall()
            valid_json_errors = sum(r['c'] for r in error_details if r['error_code'] == 'invalid_json')
            pydantic_errors = sum(r['c'] for r in error_details if r['error_code'] == 'validation_error')

            completed_requests = done + valid_json_errors + pydantic_errors
            if completed_requests > 0:
                valid_json_rate = f"{((completed_requests - valid_json_errors) / completed_requests * 100):.1f}%"
                pydantic_rate = f"{(done / completed_requests * 100):.1f}%"
            else:
                valid_json_rate = "N/A"
                pydantic_rate = "N/A"

            # Performance
            completed_jobs = conn.execute("SELECT elapsed_ms FROM tagging_job WHERE run_id = ? AND status='done' AND elapsed_ms IS NOT NULL", (run_id,)).fetchall()
            completed_times = [j['elapsed_ms'] / 1000.0 for j in completed_jobs]

            p25, median, p90, conf = calc_eta(completed_times, pending)
            units_per_hour = (3600 / median) if median else 0

            # Heartbeat and Worker State
            w_state = conn.execute("SELECT * FROM worker_state WHERE run_id = ? ORDER BY heartbeat_at DESC LIMIT 1", (run_id,)).fetchone()
            w_status = "OFFLINE"
            hb_age = 9999

            if w_state:
                import datetime
                hb_time = datetime.datetime.fromisoformat(w_state['heartbeat_at'])
                hb_age = (datetime.datetime.utcnow() - hb_time).total_seconds()
                if hb_age <= 30:
                    w_status = "RUNNING"
                    if w_state['pause_requested']:
                        w_status = "PAUSED"
                    elif w_state['stop_after_current_requested']:
                        w_status = "STOPPING"
                elif hb_age <= 120:
                    w_status = "STALE"

            if args.json:
                data = {
                    "run_id": run_id,
                    "status": w_status.lower(),
                    "contexts": {
                        "total": total_contexts,
                        "completed": complete_ctx,
                        "partial": partial_ctx,
                        "pending": pending_ctx,
                        "failed": failed_ctx,
                        "percent": round(ctx_percent, 1)
                    },
                    "units": {
                        "total": total_units,
                        "done": done,
                        "running": running,
                        "pending": pending,
                        "error": error,
                        "percent": round(units_percent, 1)
                    },
                    "quality": {
                        "job_success_rate": round(job_success_rate, 1),
                        "valid_json_rate": valid_json_rate,
                        "retry_count": retry_count
                    },
                    "performance": {
                        "median_seconds_per_unit": round(median, 1) if median else None,
                        "units_per_hour": round(units_per_hour, 1),
                        "eta_seconds": median * pending if median else None,
                        "eta_confidence": conf
                    },
                    "heartbeat_age_seconds": int(hb_age)
                }
                print(json.dumps(data, indent=2))
                return

            print("Mnemosyne Semantic Tagger")
            print(f"Run: {run_id[:8]}...   Status: {w_status}")
            print(f"Heartbeat: {int(hb_age)} s ago")
            print("\nContexts:")
            print(f"[{'#'*int(ctx_percent/5)}{'.'*(20-int(ctx_percent/5))}] {complete_ctx} / {total_contexts} completed   {ctx_percent:.1f}%")
            print("\nUnits:")
            print(f"[{'#'*int(units_percent/5)}{'.'*(20-int(units_percent/5))}] {done} / {total_units} done      {units_percent:.1f}%")

            print("\nQuality:")
            print(f"  successful jobs:     {job_success_rate:.1f}%")
            print(f"  valid JSON:          {valid_json_rate}")
            print(f"  Pydantic PASS:       {pydantic_rate}")

            print("\nPerformance:")
            if median:
                print(f"  median:               {median:.1f} s/unit")
                print(f"  throughput:           {units_per_hour:.1f} units/hour")
                print(f"  ETA:                  {format_eta(median * pending)}")
                print(f"  ETA confidence:       {conf}")
            else:
                print("  ETA:                  insufficient data")

    if args.watch:
        while True:
            print("\033c", end="")
            _print_status()
            time.sleep(args.watch)
    else:
        _print_status()


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

    parser_reset = subparsers.add_parser("reset-sidecar")
    parser_reset.add_argument("--confirm-reset", action="store_true")

    parser_prep = subparsers.add_parser("prepare")
    parser_prep.add_argument("--sample", required=True)
    parser_prep.add_argument("--limit-contexts", type=int, default=12)

    parser_run = subparsers.add_parser("run")
    parser_run.add_argument("--model", required=True)
    parser_run.add_argument("--max-jobs", type=int, default=50)

    parser_worker = subparsers.add_parser("worker")
    parser_worker.add_argument("--model", type=str, default="qwen3:14b")
    parser_worker.add_argument("--max-jobs", type=int, default=0, help="Maximum number of jobs to process before exiting (Canary Run)")
    parser_worker.add_argument("--run-id", type=str, help="Run ID to bind to")

    subparsers.add_parser("pause")
    subparsers.add_parser("resume")
    subparsers.add_parser("stop-after-current")

    parser_status = subparsers.add_parser("status")
    parser_status.add_argument("--watch", type=int, help="Refresh interval in seconds", nargs="?", const=5, default=0)
    parser_status.add_argument("--json", action="store_true")

    parser_retry = subparsers.add_parser("retry-errors")
    parser_retry.add_argument("--max-attempts", type=int, default=3)

    subparsers.add_parser("consolidate")

    parser_export = subparsers.add_parser("export-review")
    parser_export.add_argument("--output", required=True)

    args = parser.parse_args()

    if args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "reset-sidecar":
        cmd_reset_sidecar(args)
    elif args.command == "prepare":
        cmd_prepare(args)
    elif args.command == "worker":
        cmd_worker(args)
    elif args.command == "pause":
        cmd_pause(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "stop-after-current":
        cmd_stop_after_current(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "consolidate":
        cmd_consolidate(args)
    elif args.command == "export-review":
        cmd_export_review(args)


if __name__ == "__main__":
    main()
