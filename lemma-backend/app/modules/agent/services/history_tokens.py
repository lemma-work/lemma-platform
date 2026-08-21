"""Counting how big a conversation actually is.

The summarization library estimates tokens as ``len(text) / 4``. Measured
against the content agents actually accumulate, that estimate is wrong in the
dangerous direction:

    prose            -25%   (over-estimates: harmless)
    python source    +24%
    npm build logs   +27%
    minified JSON    +42%
    base64 blobs     +48%
    bare UUIDs       +67%

A coding agent's history is mostly the bottom five. At `chars/4` a 100k-token
compaction trigger fires when the real prompt is 130-170k, which is how a run
walks into a provider context-length rejection with compaction "enabled" —
one of the unexplained 400s in production.

So we count with the real tokenizer. `cl100k_base` is not the exact vocabulary
of every model Lemma runs (Fireworks GLM, Claude, and OpenAI all differ), but
being within a few percent everywhere beats being 50% under on the content that
matters. Compaction thresholds are coarse decisions; the point is to stop
under-counting by half.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

# Per-message framing (role, delimiters) that every provider adds and no
# text-only count captures. OpenAI documents ~3-4; 4 keeps us on the safe side.
_PER_MESSAGE_OVERHEAD_TOKENS = 4

# Used when the tokenizer is unavailable. Deliberately pessimistic — see the
# table above: under-counting is what causes the failure we are preventing.
_FALLBACK_CHARS_PER_TOKEN = 3.0


@lru_cache(maxsize=1)
def _encoder() -> Any:
    """The tokenizer, loaded once (~3s) and reused for the process."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # pragma: no cover - tiktoken is a hard dependency
        return None


def count_text_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _encoder()
    if encoder is None:  # pragma: no cover - see _encoder
        return int(len(text) / _FALLBACK_CHARS_PER_TOKEN) + 1
    return len(encoder.encode(text, disallowed_special=()))


def _part_text(part: object) -> str:
    """Every piece of a message part that costs tokens on the wire.

    Tool calls and returns are the expensive parts of an agent transcript and
    the easiest to miss: their payload is structured, not a `content` string.
    """
    chunks: list[str] = []
    for attribute in ("content", "text", "tool_name", "summary"):
        value = getattr(part, attribute, None)
        if isinstance(value, str):
            chunks.append(value)
        elif value is not None and attribute == "content":
            chunks.append(_stringify(value))
    for attribute in ("args", "args_json", "tool_result"):
        value = getattr(part, attribute, None)
        if value is not None:
            chunks.append(_stringify(value))
    return "\n".join(chunk for chunk in chunks if chunk)


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        # Binary parts (images) are not text tokens; their cost is provider
        # specific and is not modelled here. Count the reference, not the bytes.
        return ""
    try:
        return json.dumps(value, default=str)
    except TypeError, ValueError:  # pragma: no cover - defensive
        return str(value)


def count_model_message_tokens(messages: Sequence[object]) -> int:
    """Token count for a pydantic-ai message history."""
    total = 0
    for message in messages:
        total += _PER_MESSAGE_OVERHEAD_TOKENS
        for part in getattr(message, "parts", ()) or ():
            total += count_text_tokens(_part_text(part))
    return total


def count_stored_message_tokens(messages: Sequence[object]) -> int:
    """Token count for Lemma's own flat ``Message`` rows.

    Used before the harness converts them, so a run's history can be budgeted at
    run granularity — which is what keeps the prompt prefix stable.
    """
    total = 0
    for message in messages:
        total += _PER_MESSAGE_OVERHEAD_TOKENS
        text = getattr(message, "text", None)
        if isinstance(text, str):
            total += count_text_tokens(text)
        for attribute in ("tool_args", "tool_result"):
            value = getattr(message, attribute, None)
            if value is not None:
                total += count_text_tokens(_stringify(value))
    return total
