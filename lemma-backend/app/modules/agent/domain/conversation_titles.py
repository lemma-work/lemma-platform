"""The rule for a conversation title a person typed.

Titles arrive two ways. `ConversationTitleService` writes one from the opening
messages, capped at 80 characters and only ever when the field is empty; a
person writes the other, and this is the rule for theirs.

Blank is not a title, it is the *absence* of one -- and absence is meaningful
here, because auto-titling fires on a null title. So clearing the field hands
the conversation back to the generator rather than leaving it called "  ".
"""

from __future__ import annotations

from app.modules.agent.domain.errors import ConversationValidationError


#: The column is ``String(255)``. Longer input is refused rather than truncated:
#: a title silently cut in half is a worse answer than a rejected one, and the
#: database would otherwise raise something no caller can read.
CONVERSATION_TITLE_MAX_LENGTH = 255


def normalize_conversation_title(title: str | None) -> str | None:
    """The title as it will be stored -- ``None`` for anything blank."""
    if title is None:
        return None

    normalized = title.strip()
    if not normalized:
        return None
    if len(normalized) > CONVERSATION_TITLE_MAX_LENGTH:
        raise ConversationValidationError(
            f"Conversation title must be {CONVERSATION_TITLE_MAX_LENGTH} "
            "characters or fewer"
        )
    return normalized


__all__ = ["CONVERSATION_TITLE_MAX_LENGTH", "normalize_conversation_title"]
