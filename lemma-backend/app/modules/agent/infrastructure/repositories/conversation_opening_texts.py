"""The two message bodies a conversation title is derived from.

Split from `ConversationRepository` the way `ConversationRunQueriesMixin` and
`ConversationApprovalQueriesMixin` already were: this is one read, used by one
caller (`ConversationTitleService`), and it brings a query builder and a return
type along with it that nothing else in the repository touches.

`ConversationOpeningTexts` is re-exported from `conversation_repository` so the
import path callers already use keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, union_all

from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.models import MessageModel


# Python's ``str.strip()`` set, so the SQL blank test and the caller's agree.
_ASCII_WHITESPACE = " \t\n\r\v\f"


@dataclass(frozen=True, slots=True)
class ConversationOpeningTexts:
    """The two message bodies a conversation title is derived from."""

    user_text: str | None = None
    assistant_text: str | None = None


def _first_text_of_role(conversation_id: UUID, role: str):
    """The earliest non-blank text message for ``role``, as one indexed row."""
    return (
        select(MessageModel.role, MessageModel.text)
        .where(
            MessageModel.conversation_id == conversation_id,
            MessageModel.role == role,
            MessageModel.kind == MessageKind.TEXT.value,
            MessageModel.text.is_not(None),
            func.btrim(MessageModel.text, _ASCII_WHITESPACE) != "",
        )
        .order_by(MessageModel.sequence)
        .limit(1)
    )


class ConversationOpeningTextsMixin:
    async def get_conversation_opening_texts(
        self, conversation_id: UUID
    ) -> ConversationOpeningTexts:
        """The first user message and first assistant reply, and nothing else.

        Titling needs exactly two strings. Reading them through
        ``include_messages=True`` loaded the whole transcript and turned every
        row into an entity first — on a long thread, seconds of Python inside an
        open transaction to answer a question about two rows. Same
        shape of fix as the one recorded above for ``include_runs``.

        Each leg stops at its first qualifying row on
        ``ix_agent_message_conversation_sequence``. Blank bodies are skipped in
        SQL so a whitespace-only message does not shadow the real first one,
        which is what the caller's row-by-row scan used to do.
        """
        rows = await self.session.execute(
            union_all(
                _first_text_of_role(conversation_id, MessageRole.USER.value),
                _first_text_of_role(conversation_id, MessageRole.ASSISTANT.value),
            )
        )
        texts = {role: (text or "").strip() or None for role, text in rows.all()}
        return ConversationOpeningTexts(
            user_text=texts.get(MessageRole.USER.value),
            assistant_text=texts.get(MessageRole.ASSISTANT.value),
        )


__all__ = ["ConversationOpeningTexts", "ConversationOpeningTextsMixin"]
