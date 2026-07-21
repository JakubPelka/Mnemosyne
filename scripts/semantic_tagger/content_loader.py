import sqlite3
import json
from dataclasses import dataclass
from typing import Tuple

from pathlib import Path

from scripts.semantic_tagger.unit_serializer import (
    compute_reconstructed_content_hash,
    serialize_semantic_unit,
)
from scripts.semantic_tagger.prompt_builder import analyze_content_signals

MAIN_DB_URI = "file:data/mnemosyne.sqlite3?mode=ro"


@dataclass(frozen=True)
class ReconstructedUnit:
    unit_id: str
    context_id: str
    content: str
    content_hash: str
    event_ids: Tuple[str, ...]
    contains_code: bool
    contains_logs: bool
    contains_urls: bool
    language_hint: Tuple[str, ...]


def load_and_reconstruct_unit(
    unit_id: str,
    sidecar_db_path: str,
    schema_version: str,
    unit_strategy_version: str,
    *,
    main_db_uri: str | None = None,
) -> ReconstructedUnit:
    authoritative_main_db_uri = main_db_uri or MAIN_DB_URI
    sidecar_uri = f"{Path(sidecar_db_path).resolve().as_uri()}?mode=ro"
    with sqlite3.connect(sidecar_uri, uri=True) as sidecar_conn:
        sidecar_conn.row_factory = sqlite3.Row
        unit_row = sidecar_conn.execute(
            "SELECT * FROM tagging_unit WHERE unit_id = ?", (unit_id,)
        ).fetchone()

        if not unit_row:
            raise ValueError(f"Unit {unit_id} not found in sidecar.")

        segments_json = unit_row["segments_json"]
        if not segments_json:
            raise ValueError(f"Unit {unit_id} has no segments_json.")

        manifest = json.loads(segments_json)
        stored_content_hash = unit_row["content_hash"]
        expected_context_id = unit_row["context_id"]

    # Load title
    title_text = ""
    with sqlite3.connect(authoritative_main_db_uri, uri=True) as main_conn:
        main_conn.row_factory = sqlite3.Row

        if manifest.get("title_included"):
            # assuming title is from first event or context table, Mnemosyne seems to store title on events
            # We'll just fetch title from any event in context_id where title is not null/empty
            ctx_id = manifest.get("title_source", expected_context_id)
            ev = main_conn.execute(
                "SELECT title FROM events WHERE context_id = ? AND title IS NOT NULL "
                "AND title != '' ORDER BY timestamp_start ASC, event_id ASC LIMIT 1",
                (ctx_id,),
            ).fetchone()
            if ev:
                title_text = ev["title"]

    # Load from main DB
    segments_text = []
    event_ids = []

    with sqlite3.connect(authoritative_main_db_uri, uri=True) as main_conn:
        main_conn.row_factory = sqlite3.Row

        for seg in manifest.get("segments", []):
            event_id = seg["event_id"]
            context_id = seg.get("context_id")

            if context_id != expected_context_id:
                raise ValueError(
                    f"Event {event_id} has mismatched context_id: expected {expected_context_id}, found {context_id}"
                )

            ev = main_conn.execute(
                "SELECT text, context_id FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()

            if not ev:
                raise ValueError(f"Event {event_id} not found in main DB.")

            if ev["context_id"] != expected_context_id:
                raise ValueError(f"Event {event_id} context_id mismatch in main DB.")

            full_text = ev["text"] or ""
            start_char = seg.get("start_char", 0)
            end_char = seg.get("end_char", len(full_text))

            if not (0 <= start_char <= end_char <= len(full_text)):
                raise ValueError(
                    f"Invalid segment range for {event_id}: 0 <= {start_char} <= {end_char} <= {len(full_text)}"
                )

            segment_text = full_text[start_char:end_char]
            segments_text.append(segment_text)
            event_ids.append(event_id)

    canonical_content = serialize_semantic_unit(manifest, segments_text, title_text)

    reconstructed_hash = compute_reconstructed_content_hash(
        schema_version,
        unit_strategy_version,
        manifest,
        canonical_content,
    )

    if reconstructed_hash != stored_content_hash:
        raise ValueError(
            f"unit_content_hash_mismatch: {reconstructed_hash} != {stored_content_hash}"
        )

    signals = analyze_content_signals(canonical_content)

    # Deduplicate event IDs preserving order
    unique_event_ids = []
    for eid in event_ids:
        if eid not in unique_event_ids:
            unique_event_ids.append(eid)

    return ReconstructedUnit(
        unit_id=unit_id,
        context_id=expected_context_id,
        content=canonical_content,
        content_hash=reconstructed_hash,
        event_ids=tuple(unique_event_ids),
        contains_code=signals.contains_code,
        contains_logs=signals.contains_logs,
        contains_urls=signals.contains_urls,
        language_hint=tuple(),
    )
