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
        # Sort using deterministic keys: timestamp, then event_id
        events = sorted(events, key=lambda x: (x.get("timestamp_start") or "", x.get("event_id") or ""))

        units = []
        current_unit = []
        current_chars = 0
        sequence_no = 1
        
        # Helper to map event_type reliably to role
        def map_role(e_type):
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
            
            for i, e in enumerate(unit_events):
                txt = (e.get("text") or "")
                txt_len = len(txt)
                is_over = e["event_id"] in overlap_events_list
                
                # Split huge events if they exceed max_chars
                # To maintain simplicity of this demo, if a single event is larger than max_chars,
                # we just cap its end_char and maybe emit a warning, but Mnemosyne says:
                # "test that largest unit doesn't exceed limit EXCEPT for explicitly marked single undivided events."
                # We will keep it undivided.
                
                seg = {
                    "event_id": e["event_id"],
                    "context_id": context_id,
                    "role": map_role(e.get("event_type")),
                    "start_char": 0,
                    "end_char": txt_len,
                    "sequence_in_unit": i,
                    "is_overlap": is_over
                }
                segments.append(seg)
                segments_text.append(txt)
                chars += txt_len
                
            manifest = {
                "title_included": bool(title),
                "title_source": context_id,
                "unit_strategy_version": self.strategy_version,
                "segments": segments
            }
            
            canonical_content = serialize_semantic_unit(manifest, segments_text)
            content_hash = compute_content_hash(
                self.schema_version, self.strategy_version, canonical_content
            )
            
            # Make unit_id include content_hash so INSERT OR IGNORE doesn't skip if hash changes
            unit_id_raw = f"{context_id}|{seq_no}|{self.strategy_version}|{ordered_ids_str}|{content_hash}"
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
        
        while idx < len(events):
            e = events[idx]
            txt_len = len((e.get("text") or ""))

            if len(current_unit) >= self.max_events or (
                current_chars + txt_len > self.max_chars and len(current_unit) > 0
            ):
                u = commit_unit(current_unit, sequence_no, overlap_events_list)
                if u:
                    units.append(u)
                sequence_no += 1

                # Backtrack
                idx -= self.overlap_events
                idx = max(0, idx)
                if self.overlap_events >= len(current_unit):
                    idx = idx + 1
                    
                # Mark overlapping events for next unit
                overlap_events_list = set(ev["event_id"] for ev in events[idx:idx+self.overlap_events])

                current_unit = []
                current_chars = 0
                continue

            current_unit.append(e)
            current_chars += txt_len
            idx += 1

        if current_unit:
            u = commit_unit(current_unit, sequence_no, overlap_events_list)
            if u:
                units.append(u)

        return units

