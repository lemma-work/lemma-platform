"""The typed-state columns of conversations, runs and messages.

Each was a key of the row's metadata JSON until migration 0065. The column
shapes and the conversions between a row and the entity's typed fields
(``Conversation``, ``RunState``, ``MessageState`` document each field) live
here, apart from ``conversation``, which holds the three mapped classes that
name each other and is at its size.

Read leniently: a JSON column a backfill copied can hold anything an older
release wrote, and one unreadable value is read as absent rather than failing
whatever asked.
"""

from __future__ import annotations

from enum import Enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Text, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import MappedColumn, mapped_column

from app.modules.agent.domain.calls import CallMark
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.pod_ask_record import PodAsk
from app.modules.agent.domain.project_repo import parse_project_repo
from app.modules.agent.domain.run_state import (
    ParkedSuspension,
    RunFailure,
    RunState,
    ScheduleFiring,
)
from app.modules.agent.domain.todos import todos_from_json, todos_to_json
from app.modules.agent.domain.value_objects import (
    ConversationOrigin,
    QueueState,
    RunTrigger,
)

if TYPE_CHECKING:
    from app.modules.agent.infrastructure.models.conversation import (
        AgentRunModel,
        ConversationModel,
        MessageModel,
    )

_JSON_LIST = text("'[]'::jsonb")


def flag_column() -> MappedColumn[bool]:
    return mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


def enum_column[E: Enum](
    kind: type[E], *, length: int, default: E | None = None
) -> MappedColumn[E]:
    """An enum stored as its name in a VARCHAR, NOT NULL when it has a default."""
    return mapped_column(
        SQLEnum(kind, native_enum=False, length=length),
        nullable=default is None,
        default=default,
        server_default=default.name if default is not None else None,
    )


def text_column() -> MappedColumn[str | None]:
    return mapped_column(Text, nullable=True)


def json_column() -> MappedColumn[dict[str, object] | None]:
    return mapped_column(JSONB, nullable=True)


def json_list_column() -> MappedColumn[list[object]]:
    return mapped_column(JSONB, nullable=False, default=list, server_default=_JSON_LIST)


def timestamp_column() -> MappedColumn[datetime | None]:
    return mapped_column(DateTime(timezone=True), nullable=True)


def sequence_column() -> MappedColumn[int | None]:
    return mapped_column(BigInteger, nullable=True)


def _uuids(raw: object) -> list[UUID]:
    found: list[UUID] = []
    for value in raw if isinstance(raw, list) else ():
        try:
            found.append(UUID(str(value)))
        except ValueError:
            continue
    return found


def conversation_state_fields(row: ConversationModel) -> dict[str, object]:
    """The entity's typed fields, read off the row."""
    return {
        "origin": row.origin or ConversationOrigin.CHAT,  # None until the INSERT.
        "workflow_run_id": row.workflow_run_id,
        "schedule_type": row.schedule_type,
        "parent_run_id": row.parent_run_id,
        "spawn_key": row.spawn_key,
        "reports_to_parent": bool(row.reports_to_parent),
        "delegations_out": bool(row.delegations_out),
        "ask": PodAsk.from_json(row.ask),
        "call": CallMark.from_json(row.call),
        "cwd": row.cwd,
        "workspace_id": row.workspace_id,
        "workspace_name": row.workspace_name,
        "repo": parse_project_repo(row.repo),
        "todos": todos_from_json(row.todos),
    }


def conversation_state_values(conversation: Conversation) -> dict[str, object]:
    """The typed-state columns of ``conversation``, as they are written."""
    return {
        "origin": conversation.origin,
        "workflow_run_id": conversation.workflow_run_id,
        "schedule_type": conversation.schedule_type,
        "parent_run_id": conversation.parent_run_id,
        "spawn_key": conversation.spawn_key,
        "reports_to_parent": conversation.reports_to_parent,
        "delegations_out": conversation.delegations_out,
        "ask": conversation.ask.to_json() if conversation.ask else None,
        "call": conversation.call.to_json() if conversation.call else None,
        "cwd": conversation.cwd,
        "workspace_id": conversation.workspace_id,
        "workspace_name": conversation.workspace_name,
        "repo": conversation.repo.to_json() if conversation.repo else None,
        "todos": todos_to_json(conversation.todos),
    }


def run_state_of(row: AgentRunModel) -> RunState:
    return RunState(
        trigger=row.trigger or RunTrigger.MESSAGE,  # None until the INSERT.
        private_note=bool(row.private_note),
        asked_in_lemma=bool(row.asked_in_lemma),
        written_in=row.written_in,
        resumed_tool_call_id=row.resumed_tool_call_id,
        retried_run_id=row.retried_run_id,
        queued_behind_run_id=row.queued_behind_run_id,
        queued_leftovers_behind=_uuids(row.queued_leftovers_behind),
        stop_requested_after_sequence=row.stop_requested_after_sequence,
        delivered_to_parent_at=row.delivered_to_parent_at,
        suspended_while_parked=ParkedSuspension.from_json(row.suspended_while_parked),
        schedule_firing=ScheduleFiring.from_json(row.schedule_firing),
        failure=RunFailure.from_json(row.failure),
    )


def run_state_values(state: RunState) -> dict[str, object]:
    """The typed-state columns of a run in ``state``, as they are written."""
    return {
        **state.model_dump(
            exclude={
                "queued_leftovers_behind",
                "suspended_while_parked",
                "schedule_firing",
                "failure",
            }
        ),
        "queued_leftovers_behind": [
            str(run_id) for run_id in state.queued_leftovers_behind
        ],
        "suspended_while_parked": (
            state.suspended_while_parked.to_json()
            if state.suspended_while_parked
            else None
        ),
        "schedule_firing": (
            state.schedule_firing.to_json() if state.schedule_firing else None
        ),
        "failure": state.failure.to_json() if state.failure else None,
    }


def message_state_of(row: MessageModel) -> MessageState:
    return MessageState(
        queue_state=row.queue_state or QueueState.NONE,  # None until the INSERT.
        steered_into_run_id=row.steered_into_run_id,
        steer_dispatched_at=row.steer_dispatched_at,
        client_message_id=row.client_message_id,
        private_note=bool(row.private_note),
        private_note_in_dm=bool(row.private_note_in_dm),
        written_in=row.written_in,
        answer=row.answer,
        held_for_a_person=bool(row.held_for_a_person),
        folded_from=row.folded_from,
        synthetic_tool_return=bool(row.synthetic_tool_return),
        parked_in_run_id=row.parked_in_run_id,
    )


def message_state_values(state: MessageState) -> dict[str, object]:
    """The typed-state columns of a message in ``state``, as they are written."""
    return state.model_dump()
