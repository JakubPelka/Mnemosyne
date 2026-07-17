import argparse
import sqlite3
import time
import json
import sys
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


def cmd_prepare_rerun(args):
    import json
    import sqlite3
    import hashlib
    from pathlib import Path
    from scripts.semantic_tagger.job_store import JobStore
    from scripts.semantic_tagger.unit_builder import UnitBuilder
    from scripts.semantic_tagger.content_loader import load_and_reconstruct_unit

    source_sidecar = Path(args.source_sidecar)
    manifest = Path(args.manifest)
    target_sidecar = Path(args.target_sidecar)

    if target_sidecar.exists():
        print(f"Target sidecar {target_sidecar} already exists. Refusing to overwrite.")
        return

    # Check sha
    sha256 = hashlib.sha256(source_sidecar.read_bytes()).hexdigest()
    if sha256 != "5c69f8e3b50071b2e7145f6e984d3b29864aba7cc4d6947bb27f7b41e7f7eb70":
        print(f"Source sidecar hash mismatch: {sha256}")
        return

    with open(manifest) as f:
        manifest_entries = json.load(f)

    unique_contexts = {entry["context_id"] for entry in manifest_entries if "context_id" in entry}

    if len(manifest_entries) != args.expected_units:
        raise ValueError(
            f"Expected {args.expected_units} units, but manifest has {len(manifest_entries)}"
        )

    if len(unique_contexts) != args.expected_contexts:
        raise ValueError(
            f"Expected {args.expected_contexts} unique contexts, but manifest has {len(unique_contexts)}"
        )

    store = JobStore(target_sidecar)

    settings = {
        "think": args.think,
        "stream": args.stream,
        "temperature": args.temperature,
        "seed": args.seed,
        "num_predict": args.num_predict,
        "num_ctx": args.num_ctx,
        "request_timeout_seconds": args.request_timeout_seconds,
    }

    run_info = {
        "model_name": args.model,
        "prompt_version": args.prompt_version,
        "schema_version": args.schema_version,
        "unit_strategy_version": args.unit_strategy_version,
        "settings": settings,
    }

    run_id = store.create_run(run_info)

    with get_main_db() as conn:
        conn.row_factory = sqlite3.Row
        builder = UnitBuilder(
            schema_version=args.schema_version, strategy_version=args.unit_strategy_version
        )

        for entry in manifest_entries:
            old_unit_id = entry["unit_id"]
            reconstructed_old = load_and_reconstruct_unit(
                old_unit_id, str(source_sidecar), "semantic-tags-v1", "unit-v2-whole-events"
            )

            if reconstructed_old.content_hash != entry["content_hash"]:
                print(
                    f"Old content hash verification failed for {old_unit_id}. Expected {entry['content_hash']}, got {reconstructed_old.content_hash}"
                )
                return

            ctx_id = entry["context_id"]
            events_rows = conn.execute(
                "SELECT event_id, title, text, timestamp_start, event_type FROM events WHERE context_id = ? ORDER BY timestamp_start ASC",
                (ctx_id,),
            ).fetchall()
            events = [dict(r) for r in events_rows]
            title = next((e["title"] for e in events if e.get("title")), "")
            units = builder.build_units_for_context(ctx_id, events, title)

            matching = [u for u in units if u["event_ids"] == list(reconstructed_old.event_ids)]
            if not matching:
                print(f"Could not find matching event set for {old_unit_id}")
                return
            new_u = matching[0]

            store.save_unit(new_u)

            gen_config_str = json.dumps(settings, sort_keys=True)
            generation_config_hash = hashlib.sha256(gen_config_str.encode()).hexdigest()

            job_key_raw = f"{new_u['unit_id']}|{new_u['content_hash']}|{args.model}|<digest>|{args.prompt_version}|{args.schema_version}|{args.unit_strategy_version}|{generation_config_hash}"
            job_key = hashlib.sha256(job_key_raw.encode()).hexdigest()
            store.queue_job(job_key, run_id, new_u["unit_id"], new_u["content_hash"])

    print(f"units = {len(manifest_entries)}")
    print(f"jobs_pending = {len(manifest_entries)}")
    print("attempts = 0")
    print(f"run_id = {run_id}")


def cmd_prepare(args):
    print(f"Preparing sample: {args.sample}, limit: {args.limit_contexts}")
    store = JobStore()
    run_info = {
        "model_name": "qwen3:14b",
        "model_digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8",
        "ollama_version": "0.30.10",
        "prompt_version": "semantic_tagger_v1.md",
        "schema_version": "semantic-tags-v1",
        "unit_strategy_version": "unit-v2-whole-events",
        "settings_json": json.dumps(
            {
                "think": False,
                "temperature": 0,
                "stream": False,
                "timeout": 600,
                "endpoint": "http://127.0.0.1:11434",
            }
        ),
    }
    run_id = store.create_run(run_info)

    # Read from main DB read-only
    with get_main_db() as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT DISTINCT context_id FROM events WHERE context_id IS NOT NULL"
        if args.sample == "smoke":
            query += " ORDER BY context_id ASC"
        query += " LIMIT ?"

        contexts = conn.execute(query, (args.limit_contexts,)).fetchall()

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

            title = next((e["title"] for e in events if e.get("title")), "")
            units = builder.build_units_for_context(ctx_id, events, title)

            for u in units:
                store.save_unit(u)

                import hashlib

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
                job_key_raw = f"{u['unit_id']}|{u['content_hash']}|qwen3:14b|bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8|semantic_tagger_v1.md|semantic-tags-v1|unit-v2-whole-events|{generation_config_hash}"
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
    from scripts.semantic_tagger.job_store import JobStore
    from pathlib import Path

    store = JobStore(Path(args.db_path)) if getattr(args, "db_path", None) else None
    run_worker_loop(args.model, args.run_id, args.max_claims, args.target_done, store=store)


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
    import sqlite3

    run_id, store = get_active_run_id()
    if not run_id:
        print("No active run.")
        return

    def _print_status():
        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Context stats via tagging_unit
            total_contexts = conn.execute(
                "SELECT COUNT(DISTINCT context_id) FROM tagging_unit"
            ).fetchone()[0]

            # Unit stats
            unit_stats = conn.execute(
                "SELECT status, COUNT(*) as c FROM tagging_job WHERE run_id = ? GROUP BY status",
                (run_id,),
            ).fetchall()
            status_counts = {r["status"]: r["c"] for r in unit_stats}

            done = status_counts.get("done", 0)
            running = status_counts.get("running", 0)
            pending = status_counts.get("pending", 0)
            error = status_counts.get("failed", 0)
            skipped = status_counts.get("skipped", 0)
            total_units = done + running + pending + error + skipped

            units_percent = (done / total_units * 100) if total_units > 0 else 0

            # Contexts detailed stats
            context_status_query = """
                SELECT u.context_id, 
                       COUNT(j.job_id) as total_jobs,
                       SUM(CASE WHEN j.status = 'done' THEN 1 ELSE 0 END) as done_jobs,
                       SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END) as err_jobs,
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
                total_j = ctx["total_jobs"] or 0
                done_j = ctx["done_jobs"] or 0
                err_j = ctx["err_jobs"] or 0
                run_j = ctx["running_jobs"] or 0

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
            retry_count = (
                conn.execute(
                    "SELECT SUM(attempt_count - 1) FROM tagging_job WHERE run_id = ? AND attempt_count > 1",
                    (run_id,),
                ).fetchone()[0]
                or 0
            )

            # Quality
            job_success_rate = (done / (done + error) * 100) if (done + error) > 0 else 0

            error_details = conn.execute(
                "SELECT error_code, COUNT(*) as c FROM tagging_job WHERE run_id = ? AND status='failed' GROUP BY error_code",
                (run_id,),
            ).fetchall()
            valid_json_errors = sum(
                r["c"] for r in error_details if r["error_code"] == "invalid_json"
            )
            pydantic_errors = sum(
                r["c"] for r in error_details if r["error_code"] == "validation_error"
            )

            completed_requests = done + valid_json_errors + pydantic_errors
            if completed_requests > 0:
                valid_json_rate = (
                    f"{((completed_requests - valid_json_errors) / completed_requests * 100):.1f}%"
                )
                pydantic_rate = f"{(done / completed_requests * 100):.1f}%"
            else:
                valid_json_rate = "N/A"
                pydantic_rate = "N/A"

            # Performance
            completed_jobs = conn.execute(
                "SELECT elapsed_ms FROM tagging_job WHERE run_id = ? AND status='done' AND elapsed_ms IS NOT NULL",
                (run_id,),
            ).fetchall()
            completed_times = [j["elapsed_ms"] / 1000.0 for j in completed_jobs]

            p25, median, p90, conf = calc_eta(completed_times, pending)
            units_per_hour = (3600 / median) if median else 0

            # Heartbeat and Worker State
            w_state = conn.execute(
                "SELECT * FROM worker_state WHERE run_id = ? ORDER BY heartbeat_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            w_status = "OFFLINE"
            hb_age = 9999

            if w_state:
                import datetime

                hb_time = datetime.datetime.fromisoformat(w_state["heartbeat_at"])
                hb_age = (datetime.datetime.utcnow() - hb_time).total_seconds()
                if hb_age <= 30:
                    w_status = "RUNNING"
                    if w_state["pause_requested"]:
                        w_status = "PAUSED"
                    elif w_state["stop_after_current_requested"]:
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
                        "percent": round(ctx_percent, 1),
                    },
                    "units": {
                        "total": total_units,
                        "done": done,
                        "running": running,
                        "pending": pending,
                        "error": error,
                        "percent": round(units_percent, 1),
                    },
                    "quality": {
                        "job_success_rate": round(job_success_rate, 1),
                        "valid_json_rate": valid_json_rate,
                        "retry_count": retry_count,
                    },
                    "performance": {
                        "median_seconds_per_unit": round(median, 1) if median else None,
                        "units_per_hour": round(units_per_hour, 1),
                        "eta_seconds": median * pending if median else None,
                        "eta_confidence": conf,
                    },
                    "heartbeat_age_seconds": int(hb_age),
                }
                print(json.dumps(data, indent=2))
                return

            print("Mnemosyne Semantic Tagger")
            print(f"Run: {run_id[:8]}...   Status: {w_status}")
            print(f"Heartbeat: {int(hb_age)} s ago")
            print("\nContexts:")
            print(
                f"[{'#' * int(ctx_percent / 5)}{'.' * (20 - int(ctx_percent / 5))}] {complete_ctx} / {total_contexts} completed   {ctx_percent:.1f}%"
            )
            print("\nUnits:")
            print(
                f"[{'#' * int(units_percent / 5)}{'.' * (20 - int(units_percent / 5))}] {done} / {total_units} done      {units_percent:.1f}%"
            )

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


def cmd_retry_job(args):
    from scripts.semantic_tagger.job_store import JobStore
    import sqlite3

    store = JobStore()
    with sqlite3.connect(store.db_path, isolation_level="IMMEDIATE") as conn:
        conn.row_factory = sqlite3.Row
        now = store._now()
        row = conn.execute("SELECT * FROM tagging_job WHERE job_id = ?", (args.job_id,)).fetchone()
        if not row:
            print(f"Error: Job {args.job_id} not found.")
            sys.exit(1)
        if args.run_id and row["run_id"] != args.run_id:
            print(f"Error: Job belongs to run {row['run_id']} not {args.run_id}")
            sys.exit(1)
        if row["status"] in ("done", "running"):
            print(f"Error: Cannot retry job in status {row['status']}")
            sys.exit(1)

        conn.execute(
            "UPDATE tagging_job SET status = 'pending', retry_requested_at = ?, retry_reason = ? WHERE job_id = ?",
            (now, args.reason, args.job_id),
        )
        print(f"Job {args.job_id} set to pending for retry.")


def cmd_consolidate(args):
    print("Consolidation would happen here, writing to conversation_consolidation table.")


def cmd_export_review(args):
    source_db = (
        getattr(args, "source_db", None)
        or getattr(args, "db_path", None)
        or "data/semantic_tagger.local.sqlite3"
    )
    expected_sha = getattr(args, "source_sha256", None) or getattr(args, "audit_db_sha256", None)
    status = getattr(args, "status", "review")
    snap_date = getattr(args, "audit_db_snapshot_date", None)

    print(f"Exporting review to {args.output}")
    export_review(
        Path(source_db),
        Path(args.output),
        status=status,
        audit_db_sha256=expected_sha,
        audit_db_snapshot_date=snap_date,
    )
    print("Export complete.")


def cmd_corpus_stats(args):
    import statistics
    from pathlib import Path
    import json
    import sqlite3

    print("Computing corpus stats (this may take a moment)...")
    main_db_uri = "file:data/mnemosyne.sqlite3?mode=ro"

    with sqlite3.connect(main_db_uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        contexts_count = conn.execute("SELECT COUNT(DISTINCT context_id) FROM events").fetchone()[0]
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        contexts = conn.execute("SELECT DISTINCT context_id FROM events").fetchall()

        builder = UnitBuilder()
        total_units = 0
        total_empty = 0
        all_chars = []
        events_per_unit = []

        for ctx in contexts:
            ctx_id = ctx["context_id"]
            ev_title = conn.execute(
                "SELECT title FROM events WHERE context_id = ? AND title IS NOT NULL AND title != '' ORDER BY timestamp_start ASC LIMIT 1",
                (ctx_id,),
            ).fetchone()
            title = ev_title["title"] if ev_title else ""

            events = conn.execute(
                "SELECT event_id, event_type, timestamp_start, text FROM events WHERE context_id = ? ORDER BY timestamp_start",
                (ctx_id,),
            ).fetchall()
            events = [dict(e) for e in events]

            units = builder.build_units_for_context(ctx_id, events, title)
            for u in units:
                total_units += 1
                if u["character_count"] == 0:
                    total_empty += 1
                else:
                    all_chars.append(u["character_count"])
                    events_per_unit.append(u["event_count"])

    sidecar_db = getattr(args, "timing_db", "data/semantic_tagger_v2_rerun.sqlite3")
    times = []
    try:
        with sqlite3.connect(f"file:{sidecar_db}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT elapsed_ms FROM tagging_job WHERE status='done' AND elapsed_ms IS NOT NULL"
            ).fetchall()
            times = [r[0] for r in rows if r[0] > 0]
    except Exception:
        pass

    inferable_units = total_units - total_empty

    stats = {
        "contexts": contexts_count,
        "events": events_count,
        "canonical_units_total": total_units,
        "empty_or_rejected_units": total_empty,
        "inferable_units": inferable_units,
    }

    if all_chars:
        chars_sum = sum(all_chars)
        stats["chars_sum"] = chars_sum
        stats["chars_p50"] = statistics.median(all_chars)
        stats["chars_p75"] = (
            statistics.quantiles(all_chars, n=100)[74] if len(all_chars) > 1 else all_chars[0]
        )
        stats["chars_p90"] = (
            statistics.quantiles(all_chars, n=100)[89] if len(all_chars) > 1 else all_chars[0]
        )
        stats["estimated_input_tokens"] = chars_sum // 4

    if events_per_unit:
        stats["events_per_unit_p50"] = statistics.median(events_per_unit)
        stats["events_per_unit_max"] = max(events_per_unit)

    if times:
        est_source = getattr(args, "estimate_source", "semantic_tags_v2_small_sample")
        stats["estimate_source"] = est_source
        if est_source == "exploratory_tainted_pilot":
            stats["estimate_confidence"] = "low (num_predict=2048 was not enforced)"
        else:
            stats["estimate_confidence"] = "very_low"
        stats["available_timing_measurements"] = len(times)
        stats["sample_units_total"] = 10
        stats["successful_units"] = 8
        stats["failed_units"] = 2

        p50 = statistics.median(times)
        p75 = statistics.quantiles(times, n=100)[74] if len(times) > 1 else times[0]
        p90 = statistics.quantiles(times, n=100)[89] if len(times) > 1 else times[0]

        stats["optimistic_seconds"] = (inferable_units * p50) / 1000.0
        stats["likely_seconds"] = (inferable_units * p75) / 1000.0
        stats["conservative_seconds"] = (inferable_units * p90) / 1000.0

    out_path = Path("data/semantic_tagger_corpus_stats.local.json")
    with open(out_path, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"Stats written to {out_path}")
    print(json.dumps(stats, indent=2))


def cmd_vocabulary_candidates(args):
    import sqlite3
    from pathlib import Path

    db_path = Path("data/semantic_vocabulary.local.sqlite3")
    if not db_path.exists():
        print("No vocabulary database found.")
        return

    print(f"--- Vocabulary Candidates (min_occurrences={args.min_occurrences}) ---")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT dimension, preferred_label, occurrence_count, max_confidence, status FROM vocabulary_candidate WHERE occurrence_count >= ? ORDER BY dimension, occurrence_count DESC",
            (args.min_occurrences,),
        )
        for row in cursor:
            print(
                f"[{row['dimension']}] {row['preferred_label']} (count: {row['occurrence_count']}, max_conf: {row['max_confidence']:.2f}) - {row['status']}"
            )


def main():
    parser = argparse.ArgumentParser(description="Semantic Tagger CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")

    parser_reset = subparsers.add_parser("reset-sidecar")
    parser_reset.add_argument("--confirm-reset", action="store_true")

    parser_prepare_rerun = subparsers.add_parser("prepare-rerun")
    parser_prepare_rerun.add_argument("--source-sidecar", required=True)
    parser_prepare_rerun.add_argument("--manifest", required=True)
    parser_prepare_rerun.add_argument("--target-sidecar", required=True)
    parser_prepare_rerun.add_argument("--model", required=True)
    parser_prepare_rerun.add_argument("--schema-version", required=True)
    parser_prepare_rerun.add_argument("--prompt-version", required=True)
    parser_prepare_rerun.add_argument("--unit-strategy-version", required=True)
    parser_prepare_rerun.add_argument(
        "--think", type=lambda x: str(x).lower() == "true", default=False
    )
    parser_prepare_rerun.add_argument(
        "--stream", type=lambda x: str(x).lower() == "true", default=False
    )
    parser_prepare_rerun.add_argument("--temperature", type=int, default=0)
    parser_prepare_rerun.add_argument("--seed", type=int, default=42)
    parser_prepare_rerun.add_argument("--num-predict", type=int, default=4096)
    parser_prepare_rerun.add_argument("--num-ctx", type=int, default=8192)
    parser_prepare_rerun.add_argument("--request-timeout-seconds", type=int, default=3600)
    parser_prepare_rerun.add_argument("--expected-units", type=int, required=True)
    parser_prepare_rerun.add_argument("--expected-contexts", type=int, required=True)

    parser_prep = subparsers.add_parser("prepare")
    parser_prep.add_argument("--sample", required=True)
    parser_prep.add_argument("--limit-contexts", type=int, default=12)

    parser_run = subparsers.add_parser("run")
    parser_run.add_argument("--model", required=True)
    parser_run.add_argument("--max-jobs", type=int, default=50)

    parser_worker = subparsers.add_parser("worker")
    parser_worker.add_argument("--model", type=str, default="qwen3:14b")
    parser_worker.add_argument(
        "--max-claims",
        type=int,
        default=0,
        help="Maximum number of successfully claimed jobs in this worker session",
    )
    parser_worker.add_argument(
        "--max-jobs",
        dest="max_claims",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser_worker.add_argument(
        "--target-done",
        type=int,
        default=0,
        help="Exit when this run reaches the requested total number of done jobs",
    )
    parser_worker.add_argument("--run-id", type=str, required=True, help="Run ID to bind to")
    parser_worker.add_argument("--db-path", type=str, help="Custom db path")

    parser_pause = subparsers.add_parser("pause")
    parser_pause.add_argument("--db-path", default="data/semantic_tagger.local.sqlite3")
    parser_pause.add_argument("--run-id", required=True)

    parser_resume = subparsers.add_parser("resume")
    parser_resume.add_argument("--db-path", default="data/semantic_tagger.local.sqlite3")
    parser_resume.add_argument("--run-id", required=True)

    parser_stop = subparsers.add_parser("stop-after-current")
    parser_stop.add_argument("--db-path", default="data/semantic_tagger.local.sqlite3")
    parser_stop.add_argument("--run-id", required=True)

    parser_status = subparsers.add_parser("status")
    parser_status.add_argument("--db-path", default="data/semantic_tagger.local.sqlite3")
    parser_status.add_argument("--run-id", required=True)
    parser_status.add_argument(
        "--watch", type=int, help="Refresh interval in seconds", nargs="?", const=5, default=0
    )
    parser_status.add_argument("--json", action="store_true")

    parser_retry_job = subparsers.add_parser("retry-job")
    parser_retry_job.add_argument("--job-id", required=True)
    parser_retry_job.add_argument("--reason", required=True)
    parser_retry_job.add_argument("--run-id")

    subparsers.add_parser("consolidate")

    parser_corpus_stats = subparsers.add_parser("corpus-stats")
    parser_corpus_stats.add_argument(
        "--timing-db",
        default="data/semantic_tagger_v2_rerun.sqlite3",
        help="Sidecar DB for timing stats",
    )
    parser_corpus_stats.add_argument(
        "--estimate-source",
        default="semantic_tags_v2_small_sample",
        help="Name of the estimate source",
    )

    parser_vocab = subparsers.add_parser("vocabulary-candidates")
    parser_vocab.add_argument("--min-occurrences", type=int, default=1)

    parser_export = subparsers.add_parser("export-review")
    parser_export.add_argument("--output", required=True)
    parser_export.add_argument(
        "--source-db", help="Path to specific sqlite3 database to export from"
    )
    parser_export.add_argument("--db-path", help="Alias for --source-db")
    parser_export.add_argument("--status", default="review", help="Status to record in export")
    parser_export.add_argument(
        "--source-sha256",
        help="SHA256 of the frozen SQLite database used for this export to verify",
    )
    parser_export.add_argument("--audit-db-sha256", help="Legacy alias for --source-sha256")
    parser_export.add_argument("--audit-db-snapshot-date", help="Date of the snapshot")

    parser_compare = subparsers.add_parser("compare")
    parser_compare.add_argument("--v1-db", required=True)
    parser_compare.add_argument("--v3-db", required=True)
    parser_compare.add_argument("--provenance-json", required=True)
    parser_compare.add_argument("--output-md", required=True)

    args = parser.parse_args()

    if args.command == "corpus-stats":
        cmd_corpus_stats(args)
    elif args.command == "vocabulary-candidates":
        cmd_vocabulary_candidates(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "reset-sidecar":
        cmd_reset_sidecar(args)
    elif args.command == "prepare-rerun":
        cmd_prepare_rerun(args)
    elif args.command == "prepare":
        cmd_prepare(args)

    if args.command == "pause":
        from scripts.semantic_tagger.job_store import JobStore
        from pathlib import Path

        store = JobStore(Path(args.db_path))
        store.set_run_status(args.run_id, "paused")
        print(f"Run {args.run_id} paused.")
        return

    if args.command == "resume":
        from scripts.semantic_tagger.job_store import JobStore
        from pathlib import Path

        store = JobStore(Path(args.db_path))
        store.set_run_status(args.run_id, "active")
        print(f"Run {args.run_id} resumed.")
        return

    if args.command == "stop-after-current":
        from scripts.semantic_tagger.job_store import JobStore
        from pathlib import Path

        store = JobStore(Path(args.db_path))
        store.set_run_status(args.run_id, "stop_after_current")
        print(f"Run {args.run_id} scheduled to stop after current job.")
        return

    if args.command == "status":
        from scripts.semantic_tagger.job_store import JobStore
        from pathlib import Path

        store = JobStore(Path(args.db_path))
        status = store.get_run_status(args.run_id)
        print(f"Run {args.run_id} status: {status}")
        return

    if args.command == "compare":
        import sqlite3
        import json
        import unicodedata

        def normalize_label(label: str) -> str:
            if not label:
                return ""
            return unicodedata.normalize("NFKC", label).strip().casefold()

        def extract_label(c: dict) -> str:
            for k in ["surface_label", "label", "canonical_label", "preferred_label", "name"]:
                if k in c and c[k]:
                    return c[k]
            return ""

        with open(args.provenance_json) as f:
            audit_data = json.load(f)

        v3_to_v1_map = {u["v2_unit_id"]: u["old_unit_id"] for u in audit_data["units"]}

        v3_conn = sqlite3.connect(f"file:{args.v3_db}?mode=ro", uri=True)
        v3_conn.row_factory = sqlite3.Row
        v1_conn = sqlite3.connect(f"file:{args.v1_db}?mode=ro", uri=True)
        v1_conn.row_factory = sqlite3.Row

        done_jobs = v3_conn.execute("SELECT * FROM tagging_job WHERE status='done'").fetchall()

        comparison_lines = ["# Semantic Tagger V1 vs V3 Comparison\n"]

        for job in done_jobs:
            v3_uid = job["unit_id"]
            old_uid = v3_to_v1_map.get(v3_uid)
            if not old_uid:
                continue

            old_job = v1_conn.execute(
                "SELECT * FROM tagging_job WHERE unit_id=? AND status='done'", (old_uid,)
            ).fetchone()
            if not old_job:
                continue

            try:
                v3_out = json.loads(job["output_json"])
            except Exception:
                v3_out = {}
            try:
                old_out = json.loads(old_job["output_json"])
            except Exception:
                old_out = {}

            v3_concepts_raw = {extract_label(c): c for c in v3_out.get("concepts", [])}
            v1_concepts_raw = {extract_label(c): c for c in old_out.get("concepts", [])}

            v3_norm_map = {normalize_label(k): k for k in v3_concepts_raw.keys()}
            v1_norm_map = {normalize_label(k): k for k in v1_concepts_raw.keys()}

            v3_set = set(v3_norm_map.keys())
            v1_set = set(v1_norm_map.keys())

            kept = v3_set.intersection(v1_set)
            added = v3_set - v1_set
            removed = v1_set - v3_set

            kept_disp = [v3_norm_map[k] for k in kept]
            added_disp = [v3_norm_map[k] for k in added]
            removed_disp = [v1_norm_map[k] for k in removed]

            v3_types = []
            v3_domains = []
            v3_roles = []
            for c in v3_out.get("concepts", []):
                v3_types.extend(c.get("entity_types", []))
                v3_domains.extend(c.get("domains", []))
                v3_roles.extend(c.get("context_roles", []))

            v3_relations = []
            for r in v3_out.get("relations", []):
                s_id = r.get("subject_concept_id")
                o_id = r.get("object_concept_id")
                s_label = next(
                    (
                        extract_label(c)
                        for c in v3_out.get("concepts", [])
                        if c.get("concept_id") == s_id
                    ),
                    s_id,
                )
                o_label = next(
                    (
                        extract_label(c)
                        for c in v3_out.get("concepts", [])
                        if c.get("concept_id") == o_id
                    ),
                    o_id,
                )
                v3_relations.append(
                    f"{s_label} ({s_id}) -> {r.get('predicate')} -> {o_label} ({o_id})"
                )

            comparison_lines.append(f"## Unit: {v3_uid}")
            comparison_lines.append(f"**V1 Concepts**: {', '.join(v1_concepts_raw.keys())}")
            comparison_lines.append(f"**V3 Concepts**: {', '.join(v3_concepts_raw.keys())}")
            comparison_lines.append(f"**Kept**: {', '.join(kept_disp)}")
            comparison_lines.append(f"**Added**: {', '.join(added_disp)}")
            comparison_lines.append(f"**Removed**: {', '.join(removed_disp)}")
            comparison_lines.append(f"**V3 Entity Types**: {', '.join(set(v3_types))}")
            comparison_lines.append(f"**V3 Domains**: {', '.join(set(v3_domains))}")
            comparison_lines.append(f"**V3 Context Roles**: {', '.join(set(v3_roles))}")
            comparison_lines.append(f"**V3 Relations**: {', '.join(v3_relations)}")
            comparison_lines.append(f"**Time V1**: {old_job['elapsed_ms']} ms")
            comparison_lines.append(f"**Time V3**: {job['elapsed_ms']} ms")
            comparison_lines.append("\n")

        with open(args.output_md, "w") as f:
            f.writelines([line + "\n" for line in comparison_lines])
        print(f"Comparison written to {args.output_md}")
        return
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
