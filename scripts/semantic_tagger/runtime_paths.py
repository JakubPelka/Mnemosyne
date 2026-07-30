"""Fail-fast path resolution for semantic-tagger workers."""

from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_VOCABULARY_PATH = (
    REPOSITORY_ROOT / "data" / "semantic_vocabulary.local.sqlite3"
).resolve()
REQUIRED_MAIN_DB_TABLES = frozenset({"events", "chatgpt_messages"})


class WorkerRuntimePathError(ValueError):
    """A worker runtime database path is unsafe or unusable."""

    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"invalid {field}: {reason}")


@dataclass(frozen=True)
class WorkerRuntimePaths:
    main_db_path: Path
    main_db_uri: str
    vocabulary_db_path: Path


def _absolute_unresolved_path(value: str | Path, field: str) -> Path:
    try:
        expanded = Path(value).expanduser()
        return expanded if expanded.is_absolute() else Path.cwd() / expanded
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkerRuntimePathError(field, "path cannot be resolved") from error


def _reject_symlink_components(path: Path, field: str) -> None:
    """Reject symlinks before resolution can erase their presence."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise WorkerRuntimePathError(field, "path components cannot be inspected") from error
        if stat.S_ISLNK(mode):
            raise WorkerRuntimePathError(field, "symbolic links are not accepted")


def _resolved_path(value: str | Path, field: str) -> Path:
    unresolved = _absolute_unresolved_path(value, field)
    _reject_symlink_components(unresolved, field)
    try:
        return unresolved.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as error:
        raise WorkerRuntimePathError(field, "path cannot be resolved") from error


def _has_any_permission(path: Path, permission_bits: int) -> bool:
    try:
        return bool(path.stat().st_mode & permission_bits)
    except OSError:
        return False


@lru_cache(maxsize=1)
def _required_sidecar_tables() -> frozenset[str]:
    """Derive the worker table contract from the JobStore schema itself."""
    from scripts.semantic_tagger.job_store import SCHEMA

    with sqlite3.connect(":memory:") as conn:
        conn.executescript(SCHEMA)
        return frozenset(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )


def validate_worker_sidecar_path(
    sidecar_db: str | Path,
    run_id: str,
) -> Path:
    """Validate an existing worker sidecar without creating or modifying it."""
    from scripts.semantic_tagger.job_store import JobStore

    field = "--db-path"
    path = _resolved_path(sidecar_db, field)
    if not path.exists():
        raise WorkerRuntimePathError(field, "file does not exist")
    if not path.is_file():
        raise WorkerRuntimePathError(field, "path is not a regular file")
    try:
        if path.stat().st_nlink != 1:
            raise WorkerRuntimePathError(field, "hard links are not accepted")
    except OSError as error:
        raise WorkerRuntimePathError(field, "file identity cannot be verified") from error
    if not _has_any_permission(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
        raise WorkerRuntimePathError(field, "file is not readable")
    if not os.access(path, os.R_OK):
        raise WorkerRuntimePathError(field, "file is not readable")
    if not _has_any_permission(path, stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise WorkerRuntimePathError(field, "file is not writable")
    if not os.access(path, os.W_OK):
        raise WorkerRuntimePathError(field, "file is not writable")
    if not _has_any_permission(
        path.parent,
        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH,
    ) or not os.access(path.parent, os.W_OK):
        raise WorkerRuntimePathError(field, "parent is not writable")

    uri = f"{path.as_uri()}?mode=rw"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.execute("PRAGMA query_only = ON")
            if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise WorkerRuntimePathError(field, "connection is not query-only")
            quick_check = conn.execute("PRAGMA quick_check(1)").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise WorkerRuntimePathError(field, "SQLite sanity check failed")
            present = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            missing = sorted(_required_sidecar_tables() - present)
            if missing:
                raise WorkerRuntimePathError(
                    field,
                    f"required worker tables are missing: {', '.join(missing)}",
                )
            version_row = conn.execute(
                "SELECT value FROM sidecar_meta WHERE key = 'schema_version'"
            ).fetchone()
            version = int(version_row[0]) if version_row else None
            if version != JobStore.SIDECAR_DB_SCHEMA_VERSION:
                raise WorkerRuntimePathError(field, "sidecar schema version is unsupported")
            if not conn.execute(
                "SELECT 1 FROM tagging_run WHERE run_id = ?",
                (run_id,),
            ).fetchone():
                raise WorkerRuntimePathError(field, "requested run does not exist")
    except WorkerRuntimePathError:
        raise
    except (sqlite3.Error, TypeError, ValueError) as error:
        raise WorkerRuntimePathError(field, "SQLite validation failed") from error
    return path


def _validate_main_db(path: Path) -> str:
    field = "--main-db"
    if not path.exists():
        raise WorkerRuntimePathError(field, "file does not exist")
    if not path.is_file():
        raise WorkerRuntimePathError(field, "path is not a regular file")
    if not _has_any_permission(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
        raise WorkerRuntimePathError(field, "file is not readable")
    if not os.access(path, os.R_OK):
        raise WorkerRuntimePathError(field, "file is not readable")

    uri = f"{path.as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.execute("PRAGMA query_only = ON")
            if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise WorkerRuntimePathError(field, "connection is not query-only")
            quick_check = conn.execute("PRAGMA quick_check(1)").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise WorkerRuntimePathError(field, "read-only sanity check failed")
            placeholders = ",".join("?" for _ in REQUIRED_MAIN_DB_TABLES)
            present = {
                row[0]
                for row in conn.execute(
                    f"SELECT name FROM sqlite_master WHERE type = 'table' "
                    f"AND name IN ({placeholders})",
                    tuple(sorted(REQUIRED_MAIN_DB_TABLES)),
                )
            }
    except WorkerRuntimePathError:
        raise
    except sqlite3.Error as error:
        raise WorkerRuntimePathError(field, "read-only SQLite validation failed") from error

    missing = sorted(REQUIRED_MAIN_DB_TABLES - present)
    if missing:
        raise WorkerRuntimePathError(
            field,
            f"required canonical tables are missing: {', '.join(missing)}",
        )
    return uri


def _validate_vocabulary_db(path: Path) -> None:
    field = "--vocabulary-db"
    if path == CANONICAL_VOCABULARY_PATH:
        raise WorkerRuntimePathError(field, "canonical vocabulary path is not a worker target")
    if path.exists() and path.is_dir():
        raise WorkerRuntimePathError(field, "path is a directory")

    parent = path.parent
    nearest_existing = parent
    while not nearest_existing.exists() and nearest_existing != nearest_existing.parent:
        nearest_existing = nearest_existing.parent
    if not nearest_existing.is_dir():
        raise WorkerRuntimePathError(field, "parent cannot be created")
    if not _has_any_permission(
        nearest_existing,
        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH,
    ):
        raise WorkerRuntimePathError(field, "parent is not writable")
    if not os.access(nearest_existing, os.W_OK):
        raise WorkerRuntimePathError(field, "parent is not writable")

    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise WorkerRuntimePathError(field, "parent cannot be created") from error
    if not _has_any_permission(parent, stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise WorkerRuntimePathError(field, "parent is not writable")
    if not os.access(parent, os.W_OK):
        raise WorkerRuntimePathError(field, "parent is not writable")


def validate_worker_runtime_paths(
    main_db: str | Path,
    vocabulary_db: str | Path,
) -> WorkerRuntimePaths:
    """Resolve and validate both worker databases before any runtime mutation."""
    main_path = _resolved_path(main_db, "--main-db")
    vocabulary_path = _resolved_path(vocabulary_db, "--vocabulary-db")
    if main_path == vocabulary_path:
        raise WorkerRuntimePathError(
            "--vocabulary-db",
            "path must differ from --main-db",
        )
    if main_path.exists() and vocabulary_path.exists():
        try:
            if os.path.samefile(main_path, vocabulary_path):
                raise WorkerRuntimePathError(
                    "--vocabulary-db",
                    "path must differ from --main-db",
                )
        except OSError as error:
            raise WorkerRuntimePathError(
                "--vocabulary-db",
                "path identity cannot be verified",
            ) from error

    main_uri = _validate_main_db(main_path)
    _validate_vocabulary_db(vocabulary_path)
    return WorkerRuntimePaths(main_path, main_uri, vocabulary_path)
