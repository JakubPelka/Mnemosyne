from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import Event, EventSegment

_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"(`[^`\n]+`)")
_TOOL_ARTIFACT = re.compile(
    r"(?:turn\d+(?:file|search|fetch|view)\d*|filecite|search_result|tool_call|"
    r"assistant\s+to=|recipient=|sandbox:)",
    re.IGNORECASE,
)
_LOG_LINE = re.compile(
    r"^(?:Traceback \(most recent call last\):|\s*File \".*\", line \d+|"
    r"\w*(?:Error|Exception):|\[[A-Z]+\]|(?:INFO|DEBUG|WARNING|ERROR|CRITICAL)\b|"
    r"Step \d+/\d+|Successfully (?:built|installed)|npm (?:ERR!|WARN)|"
    r"[A-Za-z]:\\|/[^ ]+/[^ ]+)",
    re.IGNORECASE,
)
_TABLE_DIVIDER = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_LINK_ONLY = re.compile(r"^\s*(?:https?://\S+|\[[^]]+\]\(https?://[^)]+\))\s*$")
_SHELL_LANGUAGES = frozenset({"bash", "sh", "shell", "console", "powershell", "cmd", "zsh"})


@dataclass(frozen=True, slots=True)
class ParsedSegment:
    segment_type: str
    text: str
    language: str | None
    analysis_enabled: bool
    search_enabled: bool
    topic_weight: float


_POLICY = {
    "prose": (True, True, 1.0),
    "quote": (True, True, 0.3),
    "code": (False, True, 0.0),
    "inline_code": (False, True, 0.0),
    "shell_command": (False, True, 0.0),
    "log": (False, True, 0.0),
    "table": (False, True, 0.0),
    "tool_artifact": (False, False, 0.0),
    "link": (False, True, 0.0),
    "unknown": (False, True, 0.0),
}


def segment_text(value: str) -> tuple[ParsedSegment, ...]:
    segments: list[ParsedSegment] = []
    cursor = 0
    for match in _FENCE.finditer(value):
        _parse_plain(value[cursor : match.start()], segments)
        language = match.group(1).strip().casefold() or None
        body = match.group(2).strip("\n")
        if body:
            segment_type = _fenced_type(language, body)
            _append(segments, segment_type, body, language)
        cursor = match.end()
    _parse_plain(value[cursor:], segments)
    return tuple(segments)


def rebuild_event_segments(
    session: Session,
    *,
    analysis_run_id: str | None = None,
    event_ids: set[str] | None = None,
) -> int:
    statement = select(Event.event_id, Event.text).where(
        Event.is_active.is_(True),
        Event.text.is_not(None),
    )
    if event_ids is not None:
        if not event_ids:
            return 0
        statement = statement.where(Event.event_id.in_(event_ids))
    rows = session.execute(statement).all()
    ids = [event_id for event_id, _text in rows]
    if ids:
        session.execute(delete(EventSegment).where(EventSegment.event_id.in_(ids)))
    created_at = datetime.now(UTC)
    count = 0
    for event_id, text in rows:
        for index, segment in enumerate(segment_text(text or "")):
            session.add(
                EventSegment(
                    segment_id=_segment_id(event_id, index, segment),
                    analysis_run_id=analysis_run_id,
                    event_id=event_id,
                    segment_index=index,
                    segment_type=segment.segment_type,
                    language=segment.language,
                    text=segment.text,
                    analysis_enabled=segment.analysis_enabled,
                    search_enabled=segment.search_enabled,
                    topic_weight=segment.topic_weight,
                    created_at=created_at,
                )
            )
            count += 1
    session.flush()
    return count


def _parse_plain(value: str, segments: list[ParsedSegment]) -> None:
    blocks: list[tuple[str, list[str]]] = []
    for line in value.splitlines():
        segment_type = _plain_line_type(line)
        if not line.strip():
            if blocks and blocks[-1][1] and blocks[-1][1][-1] != "":
                blocks[-1][1].append("")
            continue
        if not blocks or blocks[-1][0] != segment_type:
            blocks.append((segment_type, [line]))
        else:
            blocks[-1][1].append(line)
    for segment_type, lines in blocks:
        body = "\n".join(lines).strip()
        if not body:
            continue
        if segment_type != "prose":
            _append(segments, segment_type, body, None)
            continue
        parts = _INLINE.split(body)
        for part in parts:
            if not part:
                continue
            if part.startswith("`") and part.endswith("`"):
                _append(segments, "inline_code", part[1:-1], None)
            elif part.strip():
                _append(segments, "prose", part.strip(), None)


def _plain_line_type(line: str) -> str:
    stripped = line.strip()
    if _TOOL_ARTIFACT.search(stripped):
        return "tool_artifact"
    if stripped.startswith(">"):
        return "quote"
    if _TABLE_DIVIDER.fullmatch(stripped) or (stripped.count("|") >= 2):
        return "table"
    if _LINK_ONLY.fullmatch(stripped):
        return "link"
    if _LOG_LINE.search(stripped):
        return "log"
    return "prose"


def _fenced_type(language: str | None, body: str) -> str:
    if _TOOL_ARTIFACT.search(body):
        return "tool_artifact"
    if language in _SHELL_LANGUAGES:
        return "log" if _looks_like_log(body) else "shell_command"
    if language in {"log", "text", "output", "traceback"} and _looks_like_log(body):
        return "log"
    return "code"


def _looks_like_log(value: str) -> bool:
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        return False
    return sum(bool(_LOG_LINE.search(line.strip())) for line in lines) >= max(1, len(lines) // 2)


def _append(
    segments: list[ParsedSegment],
    segment_type: str,
    text: str,
    language: str | None,
) -> None:
    normalized = text.strip()
    if not normalized:
        return
    analysis_enabled, search_enabled, topic_weight = _POLICY[segment_type]
    candidate = ParsedSegment(
        segment_type=segment_type,
        text=normalized,
        language=language,
        analysis_enabled=analysis_enabled,
        search_enabled=search_enabled,
        topic_weight=topic_weight,
    )
    if (
        segments
        and segments[-1].segment_type == candidate.segment_type
        and segments[-1].language == candidate.language
        and candidate.segment_type not in {"inline_code", "code", "shell_command"}
    ):
        previous = segments.pop()
        candidate = ParsedSegment(
            segment_type=previous.segment_type,
            text=f"{previous.text}\n{candidate.text}",
            language=previous.language,
            analysis_enabled=previous.analysis_enabled,
            search_enabled=previous.search_enabled,
            topic_weight=previous.topic_weight,
        )
    segments.append(candidate)


def _segment_id(event_id: str, index: int, segment: ParsedSegment) -> str:
    value = f"{event_id}:{index}:{segment.segment_type}:{segment.text}"
    return f"segment-{hashlib.sha256(value.encode()).hexdigest()}"
