"""Where in a place a line written in Lemma goes: its thread, or the place itself.

A place is answered with the event last heard there (``Source.delivery_ref``),
and that event carries the thread it was said in. Right for an answer to it;
wrong for a member's line, which goes where it was written:

* a reply in a thread goes to that thread's platform thread -- the root's own
  event when the root was heard there, or the message the root was sent as
  when it was written in Lemma;
* a line at the top of a shared place goes to the place itself, not into the
  thread of whoever spoke last, and quotes nobody.

Slack and Teams keep threads; elsewhere only the quoting changes.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Platforms whose shared places have threads.
THREADED = frozenset({"SLACK", "TEAMS"})

_TEAMS_THREAD = ";messageid="

#: A stored event: a ``ParsedInboundSurfaceEvent`` dump. Any: provider JSON
#: until ``ParsedInboundSurfaceEvent`` reads it.
type StoredEvent = dict[str, object]


def _reply_target(ref: Mapping[str, object]) -> dict[str, object]:
    target = ref.get("reply_target")
    return dict(target) if isinstance(target, Mapping) else {}


def at_place_level(ref: Mapping[str, object], platform: str) -> StoredEvent:
    """``ref`` re-aimed at the place itself: no thread, nothing quoted."""
    target = _reply_target(ref)
    if platform == "SLACK":
        target["thread_ts"] = None
    elif platform == "TEAMS":
        target["reply_to_id"] = None
        conversation = target.get("conversation_id")
        if isinstance(conversation, str):
            target["conversation_id"] = conversation.split(_TEAMS_THREAD, 1)[0]
    return {
        **ref,
        "reply_target": target,
        # The place itself is the thread a top-level message is in, as the
        # parsers say of one (and the field is required).
        "external_thread_id": str(ref.get("external_channel_id") or ""),
        "answers_inbound": False,
    }


def in_thread(
    ref: Mapping[str, object], platform: str, *, root_message_id: str
) -> StoredEvent:
    """``ref`` re-aimed at the thread under the message ``root_message_id`` --
    a Slack ``ts``, a Teams activity id -- quoting nobody."""
    if platform not in THREADED:
        return {**at_place_level(ref, platform)}
    target = _reply_target(ref)
    if platform == "SLACK":
        target["thread_ts"] = root_message_id
    else:
        target["reply_to_id"] = root_message_id
        conversation = target.get("conversation_id")
        if isinstance(conversation, str):
            base = conversation.split(_TEAMS_THREAD, 1)[0]
            target["conversation_id"] = f"{base}{_TEAMS_THREAD}{root_message_id}"
    return {
        **ref,
        "reply_target": target,
        "external_thread_id": root_message_id,
        "answers_inbound": False,
    }


__all__ = ["THREADED", "StoredEvent", "at_place_level", "in_thread"]
