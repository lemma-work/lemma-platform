"""What an approval card shows of the call being approved.

The person is asked to authorise an action they cannot see, and a card that
names only the tool ("exec_command") asks them to approve blind. A short
single-line preview of the arguments is enough to tell "list the orders" from
"delete the orders" -- and it is redacted and cut before it goes anywhere,
because it is rendered on a phone in somebody else's chat app.
"""

from __future__ import annotations

import json

from app.modules.agent_surfaces.domain.display_redaction import (
    MASK,
    is_sensitive_key,
    redact_secrets_in_text,
    redact_structure,
)

_PREVIEW_MAX_ARGS = 4
_PREVIEW_VALUE_CHARS = 90
_PREVIEW_TOTAL_CHARS = 260


def _one_line(text: str) -> str:
    # One line, and no backtick: Slack wraps the whole summary in code
    # formatting, so a backtick inside would end it early.
    return " ".join(text.replace("`", "'").split())


def _preview_value(value: object) -> str:
    # Redact the structure first: a secret nested inside an object, a list or a
    # JSON document held in a string is only findable while it is still a
    # structure, and it must be masked before anything cuts the text short.
    safe = redact_structure(value)
    text = (
        safe
        if isinstance(safe, str)
        else json.dumps(safe, default=str, ensure_ascii=False, separators=(",", ":"))
    )
    text = _one_line(redact_secrets_in_text(text))
    if len(text) > _PREVIEW_VALUE_CHARS:
        text = text[: _PREVIEW_VALUE_CHARS - 1].rstrip() + "…"
    return text


def redact_card_text(text: str) -> str:
    """Model-written card text (title, reason) with credentials masked."""
    return redact_secrets_in_text(text)


def approval_action_summary(tool_name: str, args: object) -> str | None:
    """The tool being approved and a redacted, truncated look at its arguments."""
    name = redact_secrets_in_text(tool_name.strip())
    if isinstance(args, str):
        # Some producers keep the call's arguments as a JSON document.
        try:
            args = json.loads(args)
        except ValueError:
            return name or None
    if not isinstance(args, dict) or not args:
        return name or None
    parts: list[str] = []
    for key, value in list(args.items())[:_PREVIEW_MAX_ARGS]:
        shown = MASK if is_sensitive_key(key) else _preview_value(value)
        parts.append(f"{_one_line(redact_secrets_in_text(str(key)))}={shown}")
    if len(args) > _PREVIEW_MAX_ARGS:
        parts.append("…")
    preview = ", ".join(parts)
    if len(preview) > _PREVIEW_TOTAL_CHARS:
        preview = preview[: _PREVIEW_TOTAL_CHARS - 1].rstrip() + "…"
    return f"{name or 'action'}({preview})"
