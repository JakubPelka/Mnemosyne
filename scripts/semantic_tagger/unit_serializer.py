from typing import List, Dict, Any


def normalize_unit_text(text: str) -> str:
    if not text:
        return ""
    # Stabilne \n, brak przypadkowych spacji na końcach
    lines = [line.rstrip() for line in text.split("\n")]
    # Usunięcie końcowych pustych linii
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def serialize_semantic_unit(manifest: Dict[str, Any], segments_text: List[str]) -> str:
    """
    manifest: {
        "title_included": bool,
        "title": str,
        "segments": [
            {"event_id": str, "role": str, ...},
            ...
        ]
    }
    segments_text: list of strings exactly corresponding to manifest["segments"]
    """
    parts = []

    # Title
    title = manifest.get("title", "").strip() if manifest.get("title_included") else ""
    parts.append("[CONTEXT_TITLE]")
    if title:
        parts.append(title)
    parts.append("[/CONTEXT_TITLE]")

    # Events
    for seg, text in zip(manifest.get("segments", []), segments_text):
        event_id = seg["event_id"]
        role = seg["role"]

        parts.append("")
        parts.append(f"[EVENT event_id={event_id} role={role}]")
        normalized = normalize_unit_text(text)
        if normalized:
            parts.append(normalized)
        parts.append("[/EVENT]")

    return "\n".join(parts)


def compute_content_hash(
    schema_version: str, unit_strategy_version: str, canonical_content: str
) -> str:
    from scripts.semantic_tagger.privacy import safe_hash

    content_hash_raw = f"{schema_version}|{unit_strategy_version}|{canonical_content}"
    return safe_hash(content_hash_raw)
