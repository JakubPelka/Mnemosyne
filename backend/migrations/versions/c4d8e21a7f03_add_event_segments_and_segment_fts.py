"""add event segments and segment fts

Revision ID: c4d8e21a7f03
Revises: 703c9442f139
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4d8e21a7f03"
down_revision: Union[str, Sequence[str], None] = "703c9442f139"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_segments",
        sa.Column("segment_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("segment_type", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("analysis_enabled", sa.Boolean(), nullable=False),
        sa.Column("search_enabled", sa.Boolean(), nullable=False),
        sa.Column("topic_weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "segment_type IN ('prose', 'code', 'inline_code', 'shell_command', 'log', "
            "'quote', 'table', 'tool_artifact', 'link', 'unknown')",
            name=op.f("ck_event_segments_event_segment_type"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_event_segments_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("segment_id", name=op.f("pk_event_segments")),
        sa.UniqueConstraint("event_id", "segment_index", name=op.f("uq_event_segments_event_id")),
    )
    with op.batch_alter_table("event_segments") as batch_op:
        batch_op.create_index(batch_op.f("ix_event_segments_event_id"), ["event_id"])
        batch_op.create_index(batch_op.f("ix_event_segments_segment_type"), ["segment_type"])

    op.execute(
        "CREATE VIRTUAL TABLE event_segments_fts USING fts5("
        "text, segment_type UNINDEXED, content='event_segments', content_rowid='rowid')"
    )
    op.execute(
        "CREATE TRIGGER event_segments_ai AFTER INSERT ON event_segments BEGIN "
        "INSERT INTO event_segments_fts(rowid, text, segment_type) "
        "VALUES (new.rowid, new.text, new.segment_type); END"
    )
    op.execute(
        "CREATE TRIGGER event_segments_ad AFTER DELETE ON event_segments BEGIN "
        "INSERT INTO event_segments_fts(event_segments_fts, rowid, text, segment_type) "
        "VALUES ('delete', old.rowid, old.text, old.segment_type); END"
    )
    op.execute(
        "CREATE TRIGGER event_segments_au AFTER UPDATE ON event_segments BEGIN "
        "INSERT INTO event_segments_fts(event_segments_fts, rowid, text, segment_type) "
        "VALUES ('delete', old.rowid, old.text, old.segment_type); "
        "INSERT INTO event_segments_fts(rowid, text, segment_type) "
        "VALUES (new.rowid, new.text, new.segment_type); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS event_segments_au")
    op.execute("DROP TRIGGER IF EXISTS event_segments_ad")
    op.execute("DROP TRIGGER IF EXISTS event_segments_ai")
    op.execute("DROP TABLE IF EXISTS event_segments_fts")
    with op.batch_alter_table("event_segments") as batch_op:
        batch_op.drop_index(batch_op.f("ix_event_segments_segment_type"))
        batch_op.drop_index(batch_op.f("ix_event_segments_event_id"))
    op.drop_table("event_segments")
