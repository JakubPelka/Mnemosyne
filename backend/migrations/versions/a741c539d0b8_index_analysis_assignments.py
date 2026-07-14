"""index reverse analysis assignments

Revision ID: a741c539d0b8
Revises: f315a68c9e72
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a741c539d0b8"
down_revision: Union[str, Sequence[str], None] = "f315a68c9e72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f("ix_event_candidate_terms_term_id"), "event_candidate_terms", ["term_id"])
    op.create_index(op.f("ix_event_topics_topic_id"), "event_topics", ["topic_id"])
    op.create_index(op.f("ix_topic_terms_term_id"), "topic_terms", ["term_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_topic_terms_term_id"), table_name="topic_terms")
    op.drop_index(op.f("ix_event_topics_topic_id"), table_name="event_topics")
    op.drop_index(op.f("ix_event_candidate_terms_term_id"), table_name="event_candidate_terms")
