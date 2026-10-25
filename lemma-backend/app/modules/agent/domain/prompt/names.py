"""A name somebody else chose, made safe to put in front of the model.

A contact's display name, a stranger's name in a group, a recipient on an email
thread: each is text a person outside the pod typed, and each lands in the
system prompt or beside a message. Stripped and truncated, a name can still
close a quote, start a line with ``# Runtime Context``, or open a tag, and so
read to the model as the brief rather than as somebody's name. So a name is
cut down to one short line of plain words, and where the prompt states it, it
is written as a JSON string -- unmistakably a value, never more instructions.
"""

from __future__ import annotations

import json
import unicodedata

#: Longer than any name a person goes by, short enough that a "name" cannot
#: carry a paragraph of instructions.
MAX_NAME_CHARS = 80

#: Characters that delimit text in a prompt -- quotes, backticks, angle
#: brackets -- and so could end the name early and start something else.
_DELIMITERS = frozenset("\"'`<>")


def _kept(character: str) -> str:
    if character in _DELIMITERS:
        return ""
    # Control and format characters (Cc, Cf): newlines, escapes, zero-width
    # and bidirectional overrides. A newline becomes a space, so two words
    # stay two words; the rest have nothing visible to keep.
    if unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp"):
        return " " if character.isspace() else ""
    return character


def clean_name(value: object, *, limit: int = MAX_NAME_CHARS) -> str | None:
    """``value`` as one short line of plain text, or ``None`` if nothing is left."""
    if value is None:
        return None
    text = "".join(_kept(character) for character in str(value))
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed or None


def quoted_name(value: object, *, limit: int = MAX_NAME_CHARS) -> str | None:
    """:func:`clean_name`, written as a JSON string literal for the prompt."""
    name = clean_name(value, limit=limit)
    return None if name is None else json.dumps(name, ensure_ascii=False)
