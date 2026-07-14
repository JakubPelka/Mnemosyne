"""add topic creation method

Revision ID: f315a68c9e72
Revises: e82f6b19a4d1
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f315a68c9e72"
down_revision: Union[str, Sequence[str], None] = "e82f6b19a4d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column(
            "creation_method",
            sa.String(length=32),
            server_default="legacy",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("topics", "creation_method")
