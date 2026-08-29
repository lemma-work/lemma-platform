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

That table is about *text*, and for a while this module was measured only
against text. Images are the case it got catastrophically wrong in the other
direction: a screenshot is not text, and rendering one as text counted a 129KB
JPEG as 277k tokens against a 110k ceiling. The guard then shredded healthy
conversations — original user request included — to fit a number that was never
real. Binary is now excluded from the text count and charged separately at what
a vision model actually bills. See `_stringify` and `_IMAGE_TOKENS`.
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

# What a vision model actually charges for one image.
#
# Images reaching a model are already bounded: `tools/image_payload` caps the
# long edge at 1568px before upload, because that is the resolution vision
# models downscale to anyway. At that bound both published formulas agree
# closely — OpenAI's 170-per-tile plus 85, and Anthropic's width*height/750 —
# and land around 1.4-1.9k tokens. One constant near the top of that range is
# accurate enough for a compaction threshold, which is a coarse decision, and
# cannot be wrong by orders of magnitude. Being wrong by orders of magnitude is
# the entire failure this replaces.
_IMAGE_TOKENS = 1_600


@lru_cache(maxsize=1)
def _encoder() -> Any:
    """The tokenizer, loaded once (~3s) and reused for the process."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    # tiktoken is a hard dependency, so the only realistic failures are the
    # import going missing and the vocabulary name being wrong. Anything else is
    # a bug worth seeing rather than silently degrading the count for.
    except ImportError, ValueError:  # pragma: no cover
        return None


def count_text_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _encoder()
    if encoder is None:  # pragma: no cover - see _encoder
        return int(len(text) / _FALLBACK_CHARS_PER_TOKEN) + 1
    return len(encoder.encode(text, disallowed_special=()))


def _is_binary_payload(value: object) -> bool:
    """A part carrying raw bytes — pydantic-ai's `BinaryContent` and anything
    shaped like it.

    Checked structurally rather than by isinstance so this module keeps no
    pydantic-ai import it does not otherwise need, and so a provider-specific
    wrapper with the same shape is caught too.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    # More than one field name: `BinaryContent` says `data`, but the guard being
    # one attribute wide is how the original defect worked -- a payload whose
    # bytes hang off any other name falls through to `json.dumps(default=str)`
    # and is charged at its repr, which for a 129KB image is ~387k tokens.
    return any(
        isinstance(getattr(value, name, None), (bytes, bytearray, memoryview))
        for name in ("data", "content", "bytes", "payload", "blob", "raw")
    )


def _json_default(value: object) -> str:
    """`json.dumps` fallback that never renders bytes.

    This is the hole the old `isinstance(value, bytes)` guard left open: it only
    saw a *top-level* bytes value, and pydantic-ai never produces one. It wraps
    an image in a `BinaryContent` and puts it inside a list, so the guard never
    fired and `default=str` rendered the model's repr — every escaped byte of
    the image — straight into the token count.
    """
    if _is_binary_payload(value):
        return ""
    return str(value)


def _stringify(value: object) -> str:
    """Everything in a part that costs *text* tokens, with binary left out.

    Walks the structure rather than trusting `json.dumps`, because binary can
    sit at any depth: `ToolReturnPart.content` is commonly
    `["Successfully read image", BinaryContent(...)]`, and a document viewer
    returns one `BinaryContent` per page.
    """
    if isinstance(value, str):
        return value
    if _is_binary_payload(value):
        return ""
    if isinstance(value, (list, tuple, set, frozenset)):
        return "\n".join(
            chunk for chunk in (_stringify(item) for item in value) if chunk
        )
    if isinstance(value, dict):
        chunks: list[str] = []
        for key, item in value.items():
            chunks.append(str(key))
            chunk = _stringify(item)
            if chunk:
                chunks.append(chunk)
        return "\n".join(chunks)
    try:
        return json.dumps(value, default=_json_default)
    except TypeError, ValueError:  # pragma: no cover - defensive
        return _json_default(value)


def _binary_tokens(value: object) -> int:
    """What the binary inside a part costs, at real vision prices."""
    if _is_binary_payload(value):
        return _IMAGE_TOKENS
    if isinstance(value, (list, tuple, set, frozenset)):
        return sum(_binary_tokens(item) for item in value)
    if isinstance(value, dict):
        return sum(_binary_tokens(item) for item in value.values())
    return 0


#: Attributes on a message part whose value costs tokens. `content` is the only
#: one that can carry binary; the rest are text or structured text.
_CONTENT_ATTRIBUTES = ("content", "text", "tool_name", "summary")
_PAYLOAD_ATTRIBUTES = ("args", "args_json", "tool_result")


def _part_text(part: object) -> str:
    """Every piece of a message part that costs text tokens.

    Tool calls and returns are the expensive parts of an agent transcript and
    the easiest to miss: their payload is structured, not a `content` string.
    """
    chunks: list[str] = []
    for attribute in _CONTENT_ATTRIBUTES:
        value = getattr(part, attribute, None)
        if isinstance(value, str):
            chunks.append(value)
        elif value is not None and attribute == "content":
            chunks.append(_stringify(value))
    for attribute in _PAYLOAD_ATTRIBUTES:
        value = getattr(part, attribute, None)
        if value is not None:
            chunks.append(_stringify(value))
    return "\n".join(chunk for chunk in chunks if chunk)


def _part_binary_tokens(part: object) -> int:
    """The vision cost of a part, which `_part_text` deliberately omits."""
    total = 0
    for attribute in _CONTENT_ATTRIBUTES + _PAYLOAD_ATTRIBUTES:
        value = getattr(part, attribute, None)
        if value is not None:
            total += _binary_tokens(value)
    return total


def count_model_message_tokens(messages: Sequence[object]) -> int:
    """Token count for a pydantic-ai message history."""
    total = 0
    for message in messages:
        total += _PER_MESSAGE_OVERHEAD_TOKENS
        for part in getattr(message, "parts", ()) or ():
            total += count_text_tokens(_part_text(part))
            total += _part_binary_tokens(part)
    return total
