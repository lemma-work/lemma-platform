"""The kinds and states the agent's tables record as typed columns.

A leaf: the entities, the draft a harness produces and the ORM all name these,
so nothing here imports any of them.
"""

from __future__ import annotations

from enum import Enum


class ConversationOrigin(str, Enum):
    """What a conversation is for: set once, by the code that opens it.

    Never taken from a client. It replaces the free-form ``source`` key and the
    keys whose presence used to stand for it (``is_sub_agent``, ``ask``,
    ``call``, ``workflow_run_id``, the surface binding).
    """

    #: A member in the web app or the CLI.
    CHAT = "CHAT"
    #: A project's pinned conversation (``ConversationType.PROJECT``).
    PROJECT = "PROJECT"
    #: A chat platform binding.
    SURFACE = "SURFACE"
    #: A line in one of Lemma's own channels.
    CHANNEL = "CHANNEL"
    #: Someone outside the pod, the web widget included.
    CONTACT = "CONTACT"
    WORKFLOW_STEP = "WORKFLOW_STEP"
    SCHEDULE = "SCHEDULE"
    SUBAGENT = "SUBAGENT"
    #: Another pod asking this one.
    POD_ASK = "POD_ASK"
    #: A live voice call's coordinator.
    CALL = "CALL"
    #: Opened to deliver a notification.
    NOTIFICATION = "NOTIFICATION"
    #: A template's setup chat.
    SETUP = "SETUP"


class RunTrigger(str, Enum):
    """Why a run started. Its conversation's origin says what the run is for."""

    #: A person's message.
    MESSAGE = "MESSAGE"
    #: Messages queued while the run before it worked.
    FOLLOW_UP = "FOLLOW_UP"
    #: A pause answered by a person.
    RESUME = "RESUME"
    #: A wait the agent set came due.
    WAKE = "WAKE"
    #: ``POST .../retry``. Owns no message of its own: it replays the person's
    #: turn, which stays on the run it retried.
    RETRY = "RETRY"
    #: Replies to a ``channel_post`` arrived.
    REPLIES = "REPLIES"
    #: A sub-agent's or another pod's result was handed back.
    DELEGATION_RESULT = "DELEGATION_RESULT"
    SCHEDULE_FIRE = "SCHEDULE_FIRE"
    WORKFLOW_START = "WORKFLOW_START"
    NOTIFICATION = "NOTIFICATION"


class QueueState(str, Enum):
    """Where a message sent while a run was working stands.

    Everything but ``NONE`` and ``STEERED`` is still owed an answer: a follow-up
    turn, or a run it is steered into, takes it (``infrastructure/db/
    queued_messages``).
    """

    #: Never queued.
    NONE = "NONE"
    #: Sent while a run was going; nobody has delivered it yet.
    QUEUED = "QUEUED"
    #: Delivered into a model request (``Message.state.steered_into_run_id``).
    STEERED = "STEERED"
    #: An Agent Host said it could not put it into the turn in flight; the
    #: follow-up turn carries it.
    STEER_UNDELIVERED = "STEER_UNDELIVERED"
    #: Held behind a question the run it was queued behind ended on.
    WAITING_ON_ANSWER = "WAITING_ON_ANSWER"


#: Queue states whose message is still owed an answer.
UNANSWERED_QUEUE_STATES = frozenset(
    {
        QueueState.QUEUED,
        QueueState.STEER_UNDELIVERED,
        QueueState.WAITING_ON_ANSWER,
    }
)


class AnswerKind(str, Enum):
    """What an assistant message is to its run's answer.

    ``None`` on a draft means nobody has decided yet: an assistant text
    persisted undecided is the answer (``RunMessageWriter.persist``).
    """

    #: The run's answer.
    FINAL = "FINAL"
    #: Narration on the way, which a client folds into the run's step list.
    INTERMEDIATE = "INTERMEDIATE"
    #: Sent as narration, made the answer when the run ended without one.
    LAST_WORDS = "LAST_WORDS"
    #: Explicitly not the answer: a thought, or text a later step superseded.
    NOT_FINAL = "NOT_FINAL"


#: The kinds that are a run's answer.
FINAL_ANSWER_KINDS = frozenset({AnswerKind.FINAL, AnswerKind.LAST_WORDS})


class WrittenIn(str, Enum):
    """Where a message typed in Lemma into a chat platform's conversation was
    written, stamped by the server (``domain/private_notes``)."""

    #: A member's own slice of a group.
    GROUP = "GROUP"
    #: A thread where the bot answers people outside the pod.
    OUTSIDERS = "OUTSIDERS"
