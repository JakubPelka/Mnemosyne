from typing import List, Dict, Any
from scripts.semantic_tagger.privacy import safe_hash
from scripts.semantic_tagger.unit_serializer import serialize_semantic_unit, compute_content_hash


class UnitBuilder:
    def __init__(
        self,
        max_chars=18000,
        max_events=10,
        overlap_events=1,
        strategy_version="unit-v1",
        schema_version="semantic-tags-v1",
    ):
        self.max_chars = max_chars
        self.max_events = max_events
        self.overlap_events = overlap_events
        self.strategy_version = strategy_version
        self.schema_version = schema_version

    def build_units_for_context(
        self, context_id: str, events: List[Dict[str, Any]], title: str = ""
    ) -> List[Dict[str, Any]]:
        events = sorted(events, key=lambda x: x.get("timestamp_start") or "")

        units = []
        current_unit = []
        current_chars = 0
        sequence_no = 1

        # We need a way to track sequence in unit

        def commit_unit(unit_events, seq_no):
            if not unit_events:
                return None

            event_ids = [e["event_id"] for e in unit_events]
            ordered_ids_str = ",".join(event_ids)

            # Deterministic ID
            unit_id_raw = f"{context_id}|{seq_no}|{self.strategy_version}|{ordered_ids_str}"
            unit_id = f"unit-{safe_hash(unit_id_raw)}"

            segments = []
            segments_text = []
            chars = 0

            for i, e in enumerate(unit_events):
                txt = e.get("text") or ""
                txt_len = len(txt)
                seg = {
                    "event_id": e["event_id"],
                    "context_id": context_id,
                    "role": e.get("event_type") or "unknown",
                    "start_char": 0,
                    "end_char": txt_len,
                    "sequence_in_unit": i,
                    "is_overlap": False,  # For this simple builder, we don't mark overlap accurately yet
                }
                segments.append(seg)
                segments_text.append(txt)
                chars += txt_len

            manifest = {
                "title_included": bool(title),
                "title": title,
                "unit_strategy_version": self.strategy_version,
                "segments": segments,
            }

            canonical_content = serialize_semantic_unit(manifest, segments_text)
            content_hash = compute_content_hash(
                self.schema_version, self.strategy_version, canonical_content
            )

            return {
                "unit_id": unit_id,
                "context_id": context_id,
                "sequence_no": seq_no,
                "content_hash": content_hash,
                "event_ids": event_ids,
                "segments": manifest,
                "event_count": len(event_ids),
                "character_count": chars,
                "estimated_token_count": chars // 4,
                "first_event_at": unit_events[0].get("timestamp_start"),
                "last_event_at": unit_events[-1].get("timestamp_start"),
                "title": title,
            }

        idx = 0
        while idx < len(events):
            e = events[idx]
            txt_len = len((e.get("text") or ""))

            if len(current_unit) >= self.max_events or (
                current_chars + txt_len > self.max_chars and len(current_unit) > 0
            ):
                # Commit current
                u = commit_unit(current_unit, sequence_no)
                if u:
                    units.append(u)
                sequence_no += 1

                # Backtrack for overlap
                idx -= self.overlap_events
                idx = max(0, idx)  # Prevent infinite loops
                # Need to be careful if overlap >= max_events, advance by at least 1
                if self.overlap_events >= len(current_unit):
                    idx = idx + 1

                current_unit = []
                current_chars = 0
                continue

            current_unit.append(e)
            current_chars += txt_len
            idx += 1

        if current_unit:
            u = commit_unit(current_unit, sequence_no)
            if u:
                units.append(u)

        return units
