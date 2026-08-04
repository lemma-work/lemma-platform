"""Render a structured final answer as the message text a human reads.

Shared by both harnesses on purpose: this string is what
``agent_surfaces.progress_observer`` delivers to Slack/Teams/Telegram, so the
same structured output must not read differently depending on which harness
produced it.
"""

from __future__ import annotations

import json

# Keys that, when present, already hold the human-readable form of the answer.
_TEXT_KEYS = ("answer", "content", "message", "summary")


def final_answer_text(data: object, *, fallback: str | None = None) -> str:
    """The human-facing text for a final answer payload."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in _TEXT_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        if data:
            return json.dumps(data, indent=2, default=str)
    if data is None:
        return fallback or ""
    return str(data)


__all__ = ["final_answer_text"]
