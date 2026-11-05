"""The message-metadata keys that track a message sent while a run was working.

A message typed while a run is in flight joins that run, which loaded its
history before the message existed. Until something delivers it, it is
*queued*: the person has said it and the agent has not seen it. These keys are
how every party -- the in-process capability, the Agent Host harness, the
follow-up turn and the client drawing the transcript -- agrees on which state a
message is in, without a column of its own.

- ``during_active_run`` is stamped by ``TurnCoordinator.start`` on append.
- ``steered_into_run`` names the run that delivered it into a model request.
  It is the one claim that matters: set, the message has been read; unset, it
  is still owed an answer and the follow-up turn will carry it.
- ``steer_dispatched_at`` is set when the Agent Host harness hands the message
  to the host as a ``STEER_RUN``. It keeps the same message from being sent
  twice; it is not a claim, so a steer that never lands still leaves the
  message for the follow-up turn.
- ``steer_undelivered`` is the host's reason, when it reported that it could not
  put the message into the turn in flight.
- ``waiting_on_answer`` names the run whose question the message is held
  behind: that run ended by asking the person something, and nothing answers
  what was said meanwhile until they answer it.
- ``stop_requested_after_sequence`` is on the *run*, not a message: the newest
  message sequence when Stop was pressed. Only what was said after it is owed
  a turn once the run has stopped.

All of these are the server's to write. A client sending any of them in a
message's metadata would decide its own delivery -- never answered, or not
withdrawable -- so they are dropped on the way in (``SERVER_OWNED_KEYS``).

Kept out of the repository so the frontend-facing names are written down once
next to what they mean, and so the harness, the service and the tests can name
them without importing SQL.
"""

from __future__ import annotations

from collections.abc import Mapping

DURING_ACTIVE_RUN = "during_active_run"
STEERED_INTO_RUN = "steered_into_run"
STEER_DISPATCHED_AT = "steer_dispatched_at"
STEER_UNDELIVERED = "steer_undelivered"
WAITING_ON_ANSWER = "waiting_on_answer"
STOP_REQUESTED_AFTER_SEQUENCE = "stop_requested_after_sequence"

#: Set by Lemma when it delivers, or holds, a message -- never by its sender.
#: ``client_message_id`` is here too: it is taken from the request's own field
#: and stamped by the server, so a value smuggled in through metadata could
#: claim another send's place in the double-submit check.
CLIENT_MESSAGE_ID = "client_message_id"
SERVER_OWNED_KEYS = frozenset(
    {
        DURING_ACTIVE_RUN,
        STEERED_INTO_RUN,
        STEER_DISPATCHED_AT,
        STEER_UNDELIVERED,
        WAITING_ON_ANSWER,
        CLIENT_MESSAGE_ID,
    }
)

# Reported by the host (and by the harness about itself) as the reason a steer
# did not land. Free text on the wire; these are the ones Lemma produces.
STEER_TURN_ENDED = "turn_ended"


def is_queued(metadata: Mapping[str, object] | None) -> bool:
    """Whether a message is still waiting for the agent to see it."""
    if not metadata:
        return False
    return metadata.get(DURING_ACTIVE_RUN) is True and not metadata.get(
        STEERED_INTO_RUN
    )


def is_withdrawable(metadata: Mapping[str, object] | None) -> bool:
    """Whether the person can still take a queued message back.

    Only while nothing is carrying it: once a ``STEER_RUN`` is on its way to the
    host the message may already be in the agent's context, and withdrawing it
    from the transcript would leave the agent answering something the person
    can no longer see. A steer the host reported as undelivered is back to
    being merely queued.
    """
    if metadata is None or not is_queued(metadata):
        return False
    return not metadata.get(STEER_DISPATCHED_AT) or bool(
        metadata.get(STEER_UNDELIVERED)
    )
