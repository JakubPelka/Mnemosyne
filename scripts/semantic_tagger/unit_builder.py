from typing import List, Dict, Any
from scripts.semantic_tagger.privacy import safe_hash
from scripts.semantic_tagger.unit_serializer import serialize_semantic_unit, compute_content_hash


class UnitBuilder:
    def __init__(
        self,
        max_chars=18000,
        max_events=10,
        overlap_events=1,
        strategy_version="unit-v2-whole-events",
        schema_version="semantic-tags-v2",
    ):
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        if overlap_events < 0:
            raise ValueError("overlap_events must be at least 0")

        self.max_chars = max_chars
        self.max_events = max_events
        self.overlap_events = overlap_events
        self.strategy_version = strategy_version
        self.schema_version = schema_version

    def build_units_for_context(
        self, context_id: str, events: List[Dict[str, Any]], title: str = ""
    ) -> List[Dict[str, Any]]:
        # Sort using deterministic keys: timestamp, then event_id
        events = sorted(
            events, key=lambda x: (x.get("timestamp_start") or "", x.get("event_id") or "")
        )

        units = []
        current_unit = []
        sequence_no = 1

        # Prefer a canonical source role when available, while preserving the
        # existing event-type mapping for source-agnostic events.
        def resolve_role(event):
            source_role = event.get("source_role")
            if source_role in ("assistant", "user", "system", "tool"):
                return source_role

            e_type = event.get("event_type")
            if e_type in ("assistant", "user", "system", "tool"):
                return e_type
            if e_type == "human":
                return "user"
            if e_type == "bot":
                return "assistant"
            return "unknown"

        def commit_unit(unit_events, seq_no, overlap_events_list):
            if not unit_events:
                return None

            event_ids = [e["event_id"] for e in unit_events]
            ordered_ids_str = ",".join(event_ids)

            segments = []
            segments_text = []
            chars = 0
            has_oversized = False

            for i, e in enumerate(unit_events):
                txt = e.get("text") or ""
                txt_len = len(txt)
                is_over = e["event_id"] in overlap_events_list

                # Report oversized single events per requirements (Variant B)
                if txt_len > self.max_chars:
                    has_oversized = True

                seg = {
                    "event_id": e["event_id"],
                    "context_id": context_id,
                    "role": resolve_role(e),
                    "start_char": 0,
                    "end_char": txt_len,
                    "sequence_in_unit": i,
                    "is_overlap": is_over,
                }
                segments.append(seg)
                segments_text.append(txt)
                chars += txt_len

            manifest = {
                "title_included": bool(title),
                "title_source": context_id,
                "unit_strategy_version": self.strategy_version,
                "segments": segments,
                "oversized_single_event": has_oversized,
            }

            # CRITICAL FIX 1: pass title explicitly to serialize_semantic_unit so hashes match reconstruct
            canonical_content = serialize_semantic_unit(manifest, segments_text, title_text=title)
            content_hash = compute_content_hash(
                self.schema_version, self.strategy_version, canonical_content
            )

            # Make unit_id include content_hash so INSERT OR IGNORE doesn't skip if hash changes
            unit_id_raw = (
                f"{context_id}|{seq_no}|{self.strategy_version}|{ordered_ids_str}|{content_hash}"
            )
            unit_id = f"unit-{safe_hash(unit_id_raw)}"

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
        overlap_events_list = set()

        last_idx = -1
        last_unit_len = -1

        while idx < len(events):
            if idx == last_idx and len(current_unit) >= last_unit_len:
                raise RuntimeError(
                    "UnitBuilder progress invariant violated (infinite loop detected)."
                )
            last_idx = idx
            last_unit_len = len(current_unit)

            e = events[idx]
            txt_len = len((e.get("text") or ""))

            current_chars = sum(len((x.get("text") or "")) for x in current_unit)

            if len(current_unit) >= self.max_events or (
                current_chars + txt_len > self.max_chars and len(current_unit) > 0
            ):
                u = commit_unit(current_unit, sequence_no, overlap_events_list)
                if u:
                    units.append(u)
                sequence_no += 1

                # CRITICAL FIX 3: overlap carry logic to avoid fake overlaps
                # Prevent infinite loop if overlap_events >= max_events
                carry_len = max(
                    0, min(self.overlap_events, self.max_events - 1, len(current_unit) - 1)
                )
                if carry_len > 0:
                    carry = current_unit[-carry_len:]
                else:
                    carry = []

                overlap_events_list = set(ev["event_id"] for ev in carry)
                current_unit = list(carry)
                continue

            current_unit.append(e)
            idx += 1

        if current_unit:
            u = commit_unit(current_unit, sequence_no, overlap_events_list)
            if u:
                units.append(u)

        return units
