"""What an approval runs, as the person asked to approve it reads it.

``request_approval`` names a tool and the arguments it runs with on approval,
with the member's own access. A card that showed only the title and reason
the agent wrote had the member approve a shell command nobody showed them.
So the line shows the call -- but as anything written for people is shown:
a value under a key naming a secret masked, a token or password inside a
value masked (``core.redaction``), and each value clipped.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.core.redaction import redact_value

#: How much of an approval's action its line shows, as text.
MAX_ACTION_CHARS = 1500
#: The longest one argument is kept in the question, and the most items of
#: a list.
MAX_ARG_CHARS = 2000
MAX_ARG_ITEMS = 50


def approval_action(args: object) -> tuple[dict[str, object], str]:
    """The arguments an approval runs its tool with, as shown, and the same
    as one text to read -- the command itself when the call is one."""
    if not isinstance(args, Mapping) or not args:
        return {}, ""
    safe = _clipped(redact_value(dict(args)))
    if len(safe) == 1 and isinstance(only := next(iter(safe.values())), str):
        text = only
    else:
        text = json.dumps(safe, ensure_ascii=False, default=str)
    if len(text) > MAX_ACTION_CHARS:
        text = text[: MAX_ACTION_CHARS - 1].rstrip() + "…"
    # Inside a fence on the line: a fence of its own would end it early.
    return safe, text.replace("```", "'''")


def _clipped(value: object) -> Any:
    if isinstance(value, str):
        return (
            value
            if len(value) <= MAX_ARG_CHARS
            else value[: MAX_ARG_CHARS - 1].rstrip() + "…"
        )
    if isinstance(value, Mapping):
        return {str(key): _clipped(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clipped(item) for item in value[:MAX_ARG_ITEMS]]
    return value


__all__ = ["MAX_ACTION_CHARS", "approval_action"]
