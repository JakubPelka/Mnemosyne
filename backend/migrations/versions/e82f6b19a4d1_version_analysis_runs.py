"""version derived analysis records

Revision ID: e82f6b19a4d1
Revises: c4d8e21a7f03
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e82f6b19a4d1"
down_revision: Union[str, Sequence[str], None] = "c4d8e21a7f03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DERIVED_TABLES = (
    "event_segments",
    "candidate_terms",
    "event_candidate_terms",
    "topics",
    "topic_terms",
    "event_topics",
    "topic_relations",
)


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("analysis_run_id", sa.String(length=128), nullable=False),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("source_event_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("analysis_run_id", name=op.f("pk_analysis_runs")),
    )
    with op.batch_alter_table("analysis_runs") as batch_op:
        batch_op.create_index(batch_op.f("ix_analysis_runs_analysis_version"), ["analysis_version"])
        batch_op.create_index(batch_op.f("ix_analysis_runs_status"), ["status"])
        batch_op.create_index(batch_op.f("ix_analysis_runs_is_active"), ["is_active"])

    op.execute(
        "INSERT INTO analysis_runs (analysis_run_id, analysis_version, status, started_at, "
        "completed_at, configuration_hash, source_event_count, is_active) "
        "VALUES ('analysis-legacy', 'legacy', 'completed', CURRENT_TIMESTAMP, "
        "CURRENT_TIMESTAMP, 'legacy', (SELECT count(*) FROM events WHERE is_active = 1), 1)"
    )
    for table in _DERIVED_TABLES:
        op.add_column(table, sa.Column("analysis_run_id", sa.String(length=128), nullable=True))
        op.create_index(op.f(f"ix_{table}_analysis_run_id"), table, ["analysis_run_id"])
        op.execute(f"UPDATE {table} SET analysis_run_id = 'analysis-legacy'")

    op.create_table(
        "topic_aliases",
        sa.Column("topic_alias_id", sa.String(length=128), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=128), nullable=False),
        sa.Column("topic_id", sa.String(length=128), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("display_alias", sa.Text(), nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.analysis_run_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.topic_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_alias_id", name=op.f("pk_topic_aliases")),
        sa.UniqueConstraint("topic_id", "normalized_alias", name=op.f("uq_topic_aliases_topic_id")),
    )
    with op.batch_alter_table("topic_aliases") as batch_op:
        batch_op.create_index(batch_op.f("ix_topic_aliases_analysis_run_id"), ["analysis_run_id"])
        batch_op.create_index(batch_op.f("ix_topic_aliases_topic_id"), ["topic_id"])
        batch_op.create_index(batch_op.f("ix_topic_aliases_normalized_alias"), ["normalized_alias"])


def downgrade() -> None:
    op.drop_table("topic_aliases")
    for table in reversed(_DERIVED_TABLES):
        op.drop_index(op.f(f"ix_{table}_analysis_run_id"), table_name=table)
        op.drop_column(table, "analysis_run_id")
    op.drop_table("analysis_runs")
