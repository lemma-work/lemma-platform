"""One tool call per id in a response.

A provider can return the same call twice under one id (Fireworks did, on
every call of a 1,399-call loop), and can return two *different* calls under
one id. pydantic-ai refuses either response outright -- the run fails with
"tool calls must have unique tool_call_id values" -- and a history storing both
pairs one return with two calls.

So a response is acted on once per id, by one rule:

* the first call under an id stands;
* a later call under it that is the same call -- same tool, same arguments --
  is a repeat, and is dropped;
* a later call under it that is a different call is a call of its own, and is
  kept under an id of its own (``distinct_call_id``). Dropping it would lose
  something the model asked for, and pair the result it is owed with nothing.

Applied twice, with this one rule -- to the response pydantic-ai executes, and
to the messages the run stores -- so the two agree on every id. They see the
same calls with one exception: a call whose arguments never parsed is executed
(and answered with the parse error) but never stored. Such calls are therefore
placed after every parsed call, so whether one was there cannot move the id of
a call that is stored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence


def call_signature(tool_name: str | None, args: object) -> str | None:
    """What makes two calls the same call, or None when the arguments do not parse.

    Arguments arrive as a JSON string from some providers and as a mapping from
    others; both spell the same call the same way here.
    """
    parsed: object = args
    if isinstance(args, str):
        if not args.strip():
            parsed = {}
        else:
            try:
                parsed = json.loads(args)
            except ValueError:
                return None
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return None
    return f"{tool_name or ''}\n{json.dumps(parsed, sort_keys=True, default=str)}"


def unparsed_signature(tool_name: str | None, args: object) -> str:
    """A signature for a call whose arguments never parsed: their raw text."""
    return f"{tool_name or ''}\n{args!s}"


def distinct_call_id(call_id: str, signature: str) -> str:
    """The id a different call under a taken id is kept under.

    Derived from the call itself rather than its position, so both places the
    rule is applied arrive at it without comparing notes.
    """
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:10]
    return f"{call_id}-{digest}"


def unique_per_call_id[T](
    items: Sequence[T],
    *,
    call_id: Callable[[T], str | None],
    signature: Callable[[T], str | None],
    raw_signature: Callable[[T], str],
    renamed: Callable[[T, str], T],
) -> tuple[list[T], list[T]]:
    """``items`` with one call per id, in their order; and the repeats dropped.

    Items with no call id (text, thinking) are always kept. ``signature``
    returns None for a call whose arguments did not parse; see the module
    docstring for why those are settled last, under ``raw_signature``.
    """
    taken: dict[str, str] = {}
    outcome: dict[int, str | None] = {}
    parsed = [(i, s) for i, s in _signed(items, call_id, signature) if s is not None]
    unparsed = [(i, s) for i, s in _signed(items, call_id, signature) if s is None]
    for index, sign in parsed:
        outcome[index] = _settle(str(call_id(items[index])), sign, taken)
    for index, _none in unparsed:
        item = items[index]
        outcome[index] = _settle(str(call_id(item)), raw_signature(item), taken)

    kept: list[T] = []
    dropped: list[T] = []
    for index, item in enumerate(items):
        if index not in outcome:
            kept.append(item)
            continue
        settled = outcome[index]
        if settled is None:
            dropped.append(item)
        elif settled == call_id(item):
            kept.append(item)
        else:
            kept.append(renamed(item, settled))
    return kept, dropped


def _signed[T](
    items: Sequence[T],
    call_id: Callable[[T], str | None],
    signature: Callable[[T], str | None],
) -> list[tuple[int, str | None]]:
    return [
        (index, signature(item))
        for index, item in enumerate(items)
        if call_id(item) is not None
    ]


def _settle(identity: str, sign: str, taken: dict[str, str]) -> str | None:
    """The id this call is kept under, or None when it repeats one already kept."""
    if identity not in taken:
        taken[identity] = sign
        return identity
    if taken[identity] == sign:
        return None
    own = distinct_call_id(identity, sign)
    if own in taken:
        # The same different call twice: a repeat of the one kept under ``own``.
        return None
    taken[own] = sign
    return own


def first_per_call_id[T](
    items: Sequence[T], call_id: Callable[[T], str | None]
) -> tuple[list[T], list[T]]:
    """``items`` with any repeat of an earlier call id dropped; and the dropped.

    Items with no call id (text, thinking) are always kept. The id alone, for
    callers that cannot tell one call from another; prefer
    ``unique_per_call_id``.
    """
    seen: set[str] = set()
    kept: list[T] = []
    dropped: list[T] = []
    for item in items:
        identity = call_id(item)
        if identity is None:
            kept.append(item)
        elif identity in seen:
            dropped.append(item)
        else:
            seen.add(identity)
            kept.append(item)
    return kept, dropped


__all__ = [
    "call_signature",
    "distinct_call_id",
    "first_per_call_id",
    "unique_per_call_id",
    "unparsed_signature",
]
