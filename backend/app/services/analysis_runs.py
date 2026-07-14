from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.models import AnalysisRun, Event

ANALYSIS_VERSION = "topic-quality-v2"


def configuration_hash(configuration: dict[str, object]) -> str:
    payload = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def start_analysis_run(session: Session, configuration: dict[str, object]) -> AnalysisRun:
    run = AnalysisRun(
        analysis_run_id=f"analysis-{uuid.uuid4()}",
        analysis_version=ANALYSIS_VERSION,
        status="running",
        started_at=datetime.now(UTC),
        completed_at=None,
        configuration_hash=configuration_hash(configuration),
        source_event_count=int(
            session.scalar(select(func.count(Event.event_id)).where(Event.is_active.is_(True))) or 0
        ),
        is_active=False,
    )
    session.add(run)
    session.flush()
    return run


def activate_analysis_run(session: Session, run: AnalysisRun) -> None:
    session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.analysis_run_id != run.analysis_run_id)
        .values(is_active=False)
    )
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.is_active = True
    session.flush()


def active_analysis_run_id(session: Session) -> str | None:
    return session.scalar(
        select(AnalysisRun.analysis_run_id).where(
            AnalysisRun.is_active.is_(True), AnalysisRun.status == "completed"
        )
    )


def active_analysis_run_subquery():
    return (
        select(AnalysisRun.analysis_run_id)
        .where(AnalysisRun.is_active.is_(True), AnalysisRun.status == "completed")
        .scalar_subquery()
    )
