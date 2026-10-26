"""One conversation's messages, read the way the runtime history queries read them.

The history assembly (``domain/runtime_history``) is handed the source it
reads, so this stands in for the repository at that seam. Its reads keep the
queries' contracts -- which messages in no run are carried, and within which
bounds -- and nothing else: the queries are held to the same contracts against
Postgres in ``test_runtime_history_load_e2e``.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.modules.agent.domain.entities import (
    AgentRun,
    Message,
    MessageKind,
    MessageRole,
    RuntimeHistoryWindow,
)
from app.modules.agent.domain.folded_turns import folded_from
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.held_turns import held_messages
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    MessageDraft,
    QueueState,
)
from app.modules.agent.domain.runtime_history import (
    MAX_HISTORY_AGENT_RUNS,
    assemble_runtime_history,
)


class StoredConversation:
    """A conversation's runs and messages, written as the server writes them."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.runs: list[AgentRun] = []
        self.messages: list[Message] = []
        self._sequence = itertools.count(1)

    def _write(self, draft: MessageDraft, *, run_id: UUID | None) -> None:
        self.messages.append(
            Message.create(
                conversation_id=self.id,
                sequence=next(self._sequence),
                agent_run_id=run_id,
                role=draft.role,
                kind=draft.kind,
                text=draft.text,
                metadata=draft.metadata,
                state=draft.state,
            )
        )

    def turn(
        self, asked: str, answered: str | None = None, *, note: bool = False
    ) -> AgentRun:
        """A run: what started it, and the agent's answer. ``note`` is a
        member's private note, which starts a private run."""
        run = AgentRun(
            conversation_id=self.id,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            started_at=datetime.now(timezone.utc),
            state=RunState(private_note=note),
        )
        self.runs.append(run)
        self._write(
            MessageDraft.of_text(
                asked, role=MessageRole.USER, state=MessageState(private_note=note)
            ),
            run_id=run.id,
        )
        if answered is not None:
            self._write(MessageDraft.of_text(answered), run_id=run.id)
        return run

    def answer(self, run: AgentRun, text: str) -> None:
        """The agent's reply in ``run``, after whatever came meanwhile."""
        self._write(MessageDraft.of_text(text), run_id=run.id)

    def followup(self) -> AgentRun:
        """A run started to answer what was said while the last one worked
        (``start_queued_followup``): nothing in it is its own yet."""
        run = AgentRun(
            conversation_id=self.id,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            started_at=datetime.now(timezone.utc),
        )
        self.runs.append(run)
        return run

    def said_meanwhile(self, run: AgentRun, text: str, *, into: AgentRun) -> None:
        """A message typed while ``run`` worked: it joins ``run``, and the
        follow-up ``into`` claims it."""
        self._write(
            MessageDraft.of_text(
                text,
                role=MessageRole.USER,
                state=MessageState(
                    queue_state=QueueState.STEERED, steered_into_run_id=into.id
                ),
            ),
            run_id=run.id,
        )

    def held(self, text: str, *, reply: str | None = None, **metadata: object) -> None:
        """What ``hold_outside_turn`` writes."""
        for draft in held_messages(text, metadata, reply=reply):
            self._write(draft, run_id=None)

    def notified(self, text: str) -> None:
        """What ``append_notification_message`` writes: a reminder or a report
        delivered to the chat, in no run."""
        self._write(
            MessageDraft.of_notification(
                text, metadata={"notification_id": str(uuid4())}
            ),
            run_id=None,
        )

    def follow_up(self, text: str) -> None:
        """A member's line to the contact, in no run."""
        self._write(
            MessageDraft.of_notification(text, metadata={"follow_up": True}),
            run_id=None,
        )

    def unmarked(self, text: str) -> None:
        """A line in no run that nothing marks as carried."""
        self._write(MessageDraft.of_text(text, role=MessageRole.USER), run_id=None)

    def folded(self, *lines: tuple[MessageRole, str]) -> UUID:
        """What ``fold_history`` writes when a merge joins another of the
        person's chats to this one: what they saw of it, in no run, each line
        marked with the conversation it came from. Returns that conversation."""
        other = uuid4()
        for role, text in lines:
            self._write(
                MessageDraft.of_text(
                    text, role=role, state=MessageState(folded_from=other)
                ),
                run_id=None,
            )
        return other

    async def attach_runtime_history_messages(
        self, runs: list[AgentRun], *, full_run_ids: set[UUID]
    ) -> list[AgentRun]:
        for run in runs:
            run.messages = [m for m in self.messages if m.agent_run_id == run.id]
        return runs

    async def load_unattached_messages(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int,
        folded_limit: int,
    ) -> list[Message]:
        """The contract of the query, not a second opinion on it."""
        within = [
            message
            for message in self.messages
            if message.agent_run_id is None
            and (after_sequence is None or message.sequence > after_sequence)
            and (before_sequence is None or message.sequence < before_sequence)
        ]
        copies = [m for m in within if folded_from(m) is not None]
        others = [
            m
            for m in within
            if folded_from(m) is None
            and (m.kind is MessageKind.NOTIFICATION or m.state.held_for_a_person)
        ]
        carried = {m.id for m in others[-limit:] + copies[-folded_limit:]}
        return [message for message in within if message.id in carried]

    async def history_for(self, run: AgentRun) -> list[Message]:
        """What the model reads for ``run``, oldest first, as the runner loads
        it: the newest runs, without their messages, then the assembly."""
        newest = [
            older.model_copy(update={"messages": []})
            for older in self.runs[-MAX_HISTORY_AGENT_RUNS:]
        ]
        window = RuntimeHistoryWindow(
            runs=newest, total_runs=len(self.runs), current_run=run
        )
        messages = await assemble_runtime_history(
            self, window, conversation_id=self.id, run_id=run.id
        )
        return sorted(messages, key=lambda message: message.sequence)
