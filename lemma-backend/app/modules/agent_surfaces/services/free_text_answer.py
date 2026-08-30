"""Remembering that somebody asked to type their answer rather than tap one.

A question rendered with native controls offers an "Other" button, and tapping
it means "I will type it". That intent lived nowhere: the tap was acknowledged
and forgotten, so the only way to honour it was to treat *every* typed message
on the surface as an answer to whatever was pending. Which is how a question
nobody ever answered came to swallow the next thing somebody said — their
instruction recorded as the answer, and no run started for it.

So the intent is written down, against the specific call it belongs to, and
spent once. Anything typed without it is what it looks like: a new message.

Kept in the conversation's own metadata rather than a new table: it is one
short-lived flag per conversation, read on the next inbound message and cleared
there, and `todo_storage` and the Agent Host's session memory already use that
seam for the same kind of state.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

#: Holds the ``tool_call_id`` of the pause whose answer is being typed.
_KEY = "surface_free_text_answer_for"


def tool_call_id_of(plan: Any) -> str:
    """The paused call a render plan belongs to.

    ``callback_id`` is ``"{conversation_id}|{tool_call_id}"`` — the shape a
    tapped button carries back, and the only place a plan keeps the id.
    """
    return str(getattr(plan, "callback_id", "") or "").partition("|")[2]


async def remember_answer_will_be_typed(
    repository: Any,
    *,
    conversation_id: UUID,
    tool_call_id: str,
) -> None:
    """Record that this pause was delivered as text, so typing is the only reply.

    Set wherever the native render did not happen — a platform with no buttons,
    or a card whose render failed and fell back to a formatted message. Asking
    the *platform* whether it supports buttons is not the same question: Slack
    supports them and still falls back when a block payload is rejected, and
    somebody looking at plain text has nothing to tap.
    """
    await remember_free_text_answer_wanted(
        repository,
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
    )


async def remember_free_text_answer_wanted(
    repository: Any,
    *,
    conversation_id: UUID,
    tool_call_id: str,
) -> None:
    """Record that the next typed message answers ``tool_call_id``."""
    if not tool_call_id:
        return
    await repository.set_conversation_metadata_key(
        conversation_id,
        _KEY,
        tool_call_id,
    )


async def free_text_answer_wanted_for(
    repository: Any,
    *,
    conversation_id: UUID,
    tool_call_id: str,
) -> bool:
    """Did somebody ask to type the answer to *this* pause?

    Matched on the call rather than merely "some free-text answer was wanted",
    so an "Other" tapped against a question two turns ago cannot be spent on an
    unrelated pause that happens to be pending now.
    """
    if not tool_call_id:
        return False
    stored = await repository.get_conversation_metadata_key(conversation_id, _KEY)
    return isinstance(stored, str) and stored == tool_call_id


async def forget_free_text_answer_wanted(
    repository: Any,
    *,
    conversation_id: UUID,
) -> None:
    """Spend the intent, so it answers one message and not every later one."""
    await repository.set_conversation_metadata_key(conversation_id, _KEY, None)


async def remember_a_prompt_that_arrived_as_words(
    repository: Any,
    *,
    conversation_id: UUID,
    envelope: Any,
    receipt: Any,
) -> None:
    """Record a question or approval that reached the person as text, not a control.

    ``PartDelivery.DEGRADED`` on ``choices``/``decision`` is exactly the
    condition :func:`remember_answer_will_be_typed` describes -- the native
    render did not happen -- so the delivery receipt answers it directly, for
    every platform and every reason. #575 recorded this from the hand-written
    text-fallback branch instead, which covered a card whose render failed but
    not email, whose single reply always carries the prompt as words and had no
    such branch to record from.

    Lives here rather than in ``surface_egress`` because it is the same subject
    as the rest of this module, and because that file is at the size ratchet's
    ceiling.
    """
    prompt = envelope.choices or envelope.decision
    if prompt is None:
        return
    if not {"choices", "decision"}.intersection(receipt.degraded):
        return
    await remember_answer_will_be_typed(
        repository,
        conversation_id=conversation_id,
        tool_call_id=tool_call_id_of(prompt),
    )
