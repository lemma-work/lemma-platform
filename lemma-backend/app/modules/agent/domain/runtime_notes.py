"""Ephemeral context appended at the end of each model request.

Runtime notes are built only while dispatching a run. Callers must not write the
rendered text back to a conversation message: keeping volatile context out of
stored history both preserves the user's original prompt and leaves the stable
prompt prefix eligible for provider caching.
"""

from __future__ import annotations

from datetime import datetime, timezone


def build_runtime_notes(*, now: datetime | None = None) -> str:
    """Render the shared, extensible runtime-notes block for an agent request."""
    current = now or datetime.now(timezone.utc)
    current_utc = current.astimezone(timezone.utc)
    timestamp = current_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "<notes>\n"
        f"Current date and time: {timestamp} (UTC).\n"
        "</notes>"
    )


def append_runtime_notes(text: str, *, now: datetime | None = None) -> str:
    """Return a transient prompt copy with runtime notes appended at the end."""
    note = build_runtime_notes(now=now)
    return f"{text}\n\n{note}" if text else note
