"""What a client calls itself, made safe to show.

A client's name is whatever it registered or its document says, and the
consent screen sets it just before the real redirect host. Right-to-left
overrides (U+202E) and other format characters can make it read as a
different sentence there; a thousand characters of it overflow a phone.
"""

from __future__ import annotations

import unicodedata

MAX_DISPLAY_NAME = 60

_INVISIBLE = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})


def display_name(raw: str | None, fallback: str) -> str:
    """``raw`` without control, format or unassigned characters, whitespace
    collapsed, cut at `MAX_DISPLAY_NAME`; ``fallback`` when nothing is left."""
    kept = "".join(
        " " if char.isspace() else char
        for char in raw or ""
        if unicodedata.category(char) not in _INVISIBLE or char.isspace()
    )
    name = " ".join(kept.split())
    if not name:
        name = fallback
    if len(name) > MAX_DISPLAY_NAME:
        name = name[: MAX_DISPLAY_NAME - 1].rstrip() + "…"
    return name
