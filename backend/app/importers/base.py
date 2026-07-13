from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class InspectionReport:
    """Structural metadata only; it must never contain source text."""

    detected_format: str
    input_kind: str
    candidate_files: tuple[str, ...]
    file_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    source_type: str
    source_version: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    source_record_id: str
    event_type: str
    timestamp_start: datetime | None
    timestamp_end: datetime | None
    title: str | None
    text: str | None
    privacy_level: str = "private"
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    """Boundary between a private source format and the shared event model."""

    def inspect(self, input_path: Path) -> InspectionReport: ...

    def validate(self, input_path: Path) -> None: ...

    def parse(self, input_path: Path) -> Iterable[Any]: ...

    def normalize(self, raw_record: Any) -> Iterable[NormalizedEvent]: ...

    def get_source_metadata(self) -> SourceMetadata: ...
