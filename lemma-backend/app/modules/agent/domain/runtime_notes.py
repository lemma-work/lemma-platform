"""Ephemeral context placed immediately before the user's turn.

Runtime notes are built only while dispatching a run. Callers must not write the
rendered text back to a conversation message: keeping volatile context out of
stored history both preserves the user's original prompt and leaves the stable
prompt prefix eligible for provider caching.

The notes go *before* the user's message, never after it. A model answers the
last user turn, so anything appended after it competes with the actual
instruction — and on Anthropic a trailing ``SystemPromptPart`` is hoisted to the
front of the system prompt, putting a value that changes every turn ahead of the
whole cacheable prefix.
"""

from __future__ import annotations

from datetime import datetime, timezone


def build_runtime_notes(*, now: datetime | None = None) -> str:
    """Render the shared, extensible runtime-notes block for an agent request."""
    current = now or datetime.now(timezone.utc)
    current_utc = current.astimezone(timezone.utc)
    timestamp = current_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"<notes>\nCurrent date and time: {timestamp} (UTC).\n</notes>"


def prepend_runtime_notes(text: str, *, now: datetime | None = None) -> str:
    """Return a transient prompt copy with runtime notes ahead of the text."""
    note = build_runtime_notes(now=now)
    return f"{note}\n\n{text}" if text else note
