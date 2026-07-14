"""add candidate term relations

Revision ID: b83e904d71c2
Revises: a741c539d0b8
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b83e904d71c2"
down_revision: Union[str, Sequence[str], None] = "a741c539d0b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_term_relations",
        sa.Column("relation_id", sa.String(length=128), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=128), nullable=False),
        sa.Column("source_term_id", sa.String(length=128), nullable=False),
        sa.Column("target_term_id", sa.String(length=128), nullable=False),
        sa.Column("shared_event_count", sa.Integer(), nullable=False),
        sa.Column("shared_context_count", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_term_id"], ["candidate_terms.term_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_term_id"], ["candidate_terms.term_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("relation_id", name=op.f("pk_candidate_term_relations")),
        sa.UniqueConstraint(
            "analysis_run_id",
            "source_term_id",
            "target_term_id",
            name=op.f("uq_candidate_term_relations_analysis_run_id"),
        ),
    )
    op.create_index(
        op.f("ix_candidate_term_relations_analysis_run_id"),
        "candidate_term_relations",
        ["analysis_run_id"],
    )
    op.create_index(
        op.f("ix_candidate_term_relations_source_term_id"),
        "candidate_term_relations",
        ["source_term_id"],
    )
    op.create_index(
        op.f("ix_candidate_term_relations_target_term_id"),
        "candidate_term_relations",
        ["target_term_id"],
    )


def downgrade() -> None:
    op.drop_table("candidate_term_relations")
