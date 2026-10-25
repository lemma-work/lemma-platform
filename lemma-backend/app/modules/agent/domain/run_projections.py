"""Minimal database projections the conversation repositories return.

Here rather than beside the queries that build them so a service can name what
it reads without importing the repository (``conversation_title_service``'s
``ConversationOpeningReader``).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationOpeningTexts:
    """The two message bodies a conversation title is derived from."""

    user_text: str | None = None
    assistant_text: str | None = None
