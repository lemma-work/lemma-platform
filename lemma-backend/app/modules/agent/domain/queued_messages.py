"""How a message sent while a run was working moves until somebody answers it.

A message typed while a run is in flight joins that run, which loaded its
history before the message existed. Until something delivers it, it is
*queued*: the person has said it and the agent has not seen it. Its
``queue_state`` (``MessageState``) is how every party -- the in-process
capability, the Agent Host harness, the follow-up turn and the client drawing
the transcript -- agrees on which state it is in:

- ``QUEUED`` is set by ``AdmitMessage`` on append.
- ``STEERED`` is set, with ``steered_into_run_id``, by whoever delivered it
  into a model request. It is the one claim that matters: steered, the message
  has been read; otherwise it is still owed an answer and the follow-up turn
  will carry it.
- ``steer_dispatched_at`` is set when the Agent Host harness hands the message
  to the host as a ``STEER_RUN``. It keeps the same message from being sent
  twice; it is not a claim, so a steer that never lands still leaves the
  message for the follow-up turn.
- ``STEER_UNDELIVERED`` is the host saying it could not put the message into
  the turn in flight.
- ``WAITING_ON_ANSWER`` holds it behind the question the run it was queued
  behind ended on: nothing answers what was said meanwhile until the person
  answers that.
- ``stop_requested_after_sequence`` is on the *run*, not a message: the newest
  message sequence when Stop was pressed. Only what was said after it is owed
  a turn once the run has stopped.

All of it is the server's to write. A client sending any of the old keys in a
message's metadata would decide its own delivery -- never answered, or not
withdrawable -- so they are dropped on the way in (``SERVER_OWNED_KEYS``), with
every other key that is a column now.
"""

from __future__ import annotations

from app.modules.agent.domain.conversations import MOVED_MESSAGE_KEYS

#: Dropped from what a caller passes as a message's metadata: every key the
#: server keeps in a column (the queue, the send id, the note marks, the answer
#: flags), and ``synthetic``, which marks a notice history assembly writes and
#: is never stored -- a stored one could only be a caller imitating it.
SERVER_OWNED_KEYS = MOVED_MESSAGE_KEYS | {"synthetic"}

#: The key the web app first sent a send's id under, before the request had a
#: field of its own; read off an old client's request, never stored.
CLIENT_MESSAGE_ID_REQUEST_KEY = "client_message_id"

# Reported by the host (and by the harness about itself) as the reason a steer
# did not land. Free text on the wire; these are the ones Lemma produces.
STEER_TURN_ENDED = "turn_ended"
