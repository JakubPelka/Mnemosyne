from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ChatGPTConversationModel(Base):
    __tablename__ = "chatgpt_conversations"

    conversation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChatGPTMessageModel(Base):
    __tablename__ = "chatgpt_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "message_id",
            name="uq_chatgpt_messages_conversation_message",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_chatgpt_messages_conversation_sequence",
        ),
    )

    message_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("chatgpt_conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    parent_message_key: Mapped[str | None] = mapped_column(
        ForeignKey("chatgpt_messages.message_key", ondelete="SET NULL")
    )
    parent_message_id: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    is_on_current_branch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
