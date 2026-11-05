"""What the server records about a message beyond its body.

Each of these was a key in the message's metadata JSON -- the queue flags, the
idempotency id, the private-note marks, what the message is to its run's
answer -- filtered and updated in SQL as if it were a column. They are columns
of ``agent_messages`` now, all of them the server's to write; this is their
typed form, on both the draft a harness produces and the stored message.

What stays in metadata is content the prompt reads as content: channel context,
media, reasoning replay, the ``source`` label.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.agent.domain.state_kinds import (
    FINAL_ANSWER_KINDS,
    UNANSWERED_QUEUE_STATES,
    AnswerKind,
    QueueState,
    WrittenIn,
)


class MessageState(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Sent while a run was working, and whether anybody has delivered it.
    queue_state: QueueState = QueueState.NONE
    #: The run that delivered it into a model request.
    steered_into_run_id: UUID | None = None
    #: When the Agent Host harness handed it to the host as a ``STEER_RUN``.
    #: Not a claim: it keeps the same message from being sent twice.
    steer_dispatched_at: datetime | None = None
    #: The sender's own id for this send; one message per id per conversation.
    client_message_id: str | None = None
    #: A person's word to the agent alone; its answer stays in Lemma.
    private_note: bool = False
    #: A private note written in a member's own direct chat with the bot.
    private_note_in_dm: bool = False
    #: Typed in Lemma into a platform's conversation, and which kind.
    written_in: WrittenIn | None = None
    #: What an assistant message is to its run's answer; ``None`` when nobody
    #: has said, or it is not the agent's to say.
    answer: AnswerKind | None = None
    #: An outsider's turn held for a person, and the handover that answered it.
    held_for_a_person: bool = False
    #: The conversation a contact merge copied it from.
    folded_from: UUID | None = None
    #: A tool return the server made up for a call nobody answered.
    synthetic_tool_return: bool = False
    #: A pausing call parked inside its own run, which stays going while a
    #: remote harness's bridge waits for the answer.
    parked_in_run_id: UUID | None = None

    @property
    def is_queued(self) -> bool:
        """Still waiting for the agent to see it."""
        return self.queue_state in UNANSWERED_QUEUE_STATES

    @property
    def is_withdrawable(self) -> bool:
        """Whether the person can still take a queued message back.

        Only while nothing is carrying it: once a ``STEER_RUN`` is on its way
        to the host the message may already be in the agent's context, and
        withdrawing it from the transcript would leave the agent answering
        something the person can no longer see. A steer the host reported as
        undelivered is back to being merely queued.
        """
        if not self.is_queued:
            return False
        return (
            self.steer_dispatched_at is None
            or self.queue_state is QueueState.STEER_UNDELIVERED
        )

    @property
    def is_final_answer(self) -> bool:
        return self.answer in FINAL_ANSWER_KINDS
