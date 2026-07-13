from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from backend.app.models import ChatGPTConversationModel, ChatGPTMessageModel, Event

_MAX_CONTEXT_MESSAGES = 10


@dataclass(frozen=True, slots=True)
class ContextMessage:
    event_id: str
    message_id: str
    role: str
    created_at: datetime | None
    text: str | None
    sequence_number: int
    is_target: bool


@dataclass(frozen=True, slots=True)
class MessageContext:
    conversation_id: str
    conversation_title: str | None
    messages: tuple[ContextMessage, ...]


def get_message_context(
    session: Session,
    event_id: str,
    *,
    before: int = 2,
    after: int = 2,
) -> MessageContext | None:
    if not 0 <= before <= _MAX_CONTEXT_MESSAGES or not 0 <= after <= _MAX_CONTEXT_MESSAGES:
        raise ValueError("context_window_out_of_range")

    target = session.scalar(
        select(ChatGPTMessageModel).where(ChatGPTMessageModel.event_id == event_id)
    )
    if target is None:
        return None

    previous: list[ChatGPTMessageModel] = []
    current = target
    for _ in range(before):
        if current.parent_message_key is None:
            break
        parent = session.get(ChatGPTMessageModel, current.parent_message_key)
        if parent is None:
            break
        previous.append(parent)
        current = parent
    previous.reverse()

    following: list[ChatGPTMessageModel] = []
    current = target
    for _ in range(after):
        child = session.scalar(
            select(ChatGPTMessageModel)
            .where(ChatGPTMessageModel.parent_message_key == current.message_key)
            .order_by(
                case((ChatGPTMessageModel.is_on_current_branch.is_(True), 0), else_=1),
                ChatGPTMessageModel.sequence_number,
            )
            .limit(1)
        )
        if child is None:
            break
        following.append(child)
        current = child

    selected = [*previous, target, *following]
    event_ids = [message.event_id for message in selected]
    events = {
        event.event_id: event
        for event in session.scalars(select(Event).where(Event.event_id.in_(event_ids)))
    }
    conversation = session.get(ChatGPTConversationModel, target.conversation_id)
    return MessageContext(
        conversation_id=target.conversation_id,
        conversation_title=conversation.title if conversation else None,
        messages=tuple(
            ContextMessage(
                event_id=message.event_id,
                message_id=message.message_id,
                role=message.role,
                created_at=message.created_at,
                text=events[message.event_id].text,
                sequence_number=message.sequence_number,
                is_target=message.event_id == event_id,
            )
            for message in selected
        ),
    )
