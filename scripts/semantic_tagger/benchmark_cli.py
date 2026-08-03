"""Thin CLI for offline semantic-tagger benchmark preparation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.semantic_tagger.benchmark import (
    BenchmarkError,
    INVENTORY_PATH,
    prepare_benchmark_artifacts,
    read_local_benchmark_salt,
    write_sidecar_inventory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded, local-only semantic benchmark checkpoints"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser(
        "inventory", help="Inspect metadata from an explicit sidecar allowlist"
    )
    inventory.add_argument("--sidecar", dest="sidecars", type=Path, action="append", required=True)
    inventory.add_argument("--salt-file", type=Path, required=True)
    inventory.add_argument("--repo-root", type=Path, default=Path.cwd())

    prepare = subparsers.add_parser(
        "prepare", help="Prepare a later human-gated synthetic benchmark review"
    )
    prepare.add_argument("--sidecar", type=Path, required=True)
    prepare.add_argument("--main-db", type=Path, required=True)
    prepare.add_argument("--salt-file", type=Path, required=True)
    prepare.add_argument("--approved-source-id", required=True)
    prepare.add_argument("--approved-run-id", required=True)
    prepare.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        secret_salt = read_local_benchmark_salt(args.repo_root, args.salt_file)
        if args.command == "inventory":
            report = write_sidecar_inventory(
                repo_root=args.repo_root,
                sidecar_allowlist=args.sidecars,
                secret_salt=secret_salt,
            )
        else:
            results = prepare_benchmark_artifacts(
                repo_root=args.repo_root,
                sidecar_path=args.sidecar,
                main_db_path=args.main_db,
                secret_salt=secret_salt,
                approved_source_id=args.approved_source_id,
                approved_run_id=args.approved_run_id,
            )
    except BenchmarkError:
        print("checkpoint_error=bounded_offline_operation_failed", file=sys.stderr)
        return 2
    except Exception:
        print("checkpoint_error=unexpected_local_failure", file=sys.stderr)
        return 2
    if args.command == "inventory":
        print(f"sidecars_discovered={report['sidecar_count']}")
        print(f"sidecars_rejected={report['unsafe_sidecar_count']}")
        print(f"compatible_runs={report['compatible_run_count']}")
        for source in report["sources"]:
            for run in source["runs"]:
                if source["source_status"] == "accepted" and run["compatible"]:
                    counts = run["terminal_job_counts"]
                    print(
                        "compatible_run="
                        f"{run['opaque_run_id']} source={source['source_id']} "
                        f"done={counts['done']} failed={counts['failed']}"
                    )
        print(f"inventory_path={INVENTORY_PATH.as_posix()}")
        return 0
    print(f"selected_cases={results['selected_cases']}")
    print(f"pending_human_reviews={results['pending_human_reviews']}")
    print(f"unresolved_risk_reports={results['unresolved_risk_reports']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
