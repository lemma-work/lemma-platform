"""What an approval card shows of the call being approved.

The person is asked to authorise an action they cannot see, and a card that
names only the tool ("exec_command") asks them to approve blind. A short
single-line preview of the arguments is enough to tell "list the orders" from
"delete the orders" -- and it is redacted and cut before it goes anywhere,
because it is rendered on a phone in somebody else's chat app.
"""

from __future__ import annotations

import json
import re

_PREVIEW_MAX_ARGS = 4
_PREVIEW_VALUE_CHARS = 90
_PREVIEW_TOTAL_CHARS = 260
_SECRET_KEY = re.compile(
    r"token|secret|passw|api[_-]?key|authorization|credential|private[_-]?key|cookie",
    re.IGNORECASE,
)
# A secret can sit inside a value the key does not give away: a shell command
# carrying a bearer header, or `--password=...`.
_SECRET_IN_VALUE = re.compile(
    r"(?i)(bearer\s+|(?:token|secret|passw\w*|api[_-]?key)\s*[=:]\s*)[^\s\"',;]+"
)


def _preview_value(value: object) -> str:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    )
    # One line, and no backtick: Slack wraps the whole summary in code
    # formatting, so a backtick inside would end it early.
    text = " ".join(text.replace("`", "'").split())
    text = _SECRET_IN_VALUE.sub(r"\1***", text)
    if len(text) > _PREVIEW_VALUE_CHARS:
        text = text[: _PREVIEW_VALUE_CHARS - 1].rstrip() + "…"
    return text


def approval_action_summary(tool_name: str, args: object) -> str | None:
    """The tool being approved and a redacted, truncated look at its arguments."""
    name = tool_name.strip()
    if not isinstance(args, dict) or not args:
        return name or None
    parts: list[str] = []
    for key, value in list(args.items())[:_PREVIEW_MAX_ARGS]:
        shown = "***" if _SECRET_KEY.search(str(key)) else _preview_value(value)
        parts.append(f"{key}={shown}")
    if len(args) > _PREVIEW_MAX_ARGS:
        parts.append("…")
    preview = ", ".join(parts)
    if len(preview) > _PREVIEW_TOTAL_CHARS:
        preview = preview[: _PREVIEW_TOTAL_CHARS - 1].rstrip() + "…"
    return f"{name or 'action'}({preview})"
