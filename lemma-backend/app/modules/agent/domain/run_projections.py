"""Minimal database projections the conversation repository returns.

Here rather than beside the queries that build them so `domain/ports.py` can
name them: a port that cannot say what a method returns cannot declare it,
and an undeclared method is how this repository's port drifted (#518).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationOpeningTexts:
    """The two message bodies a conversation title is derived from."""

    user_text: str | None = None
    assistant_text: str | None = None
