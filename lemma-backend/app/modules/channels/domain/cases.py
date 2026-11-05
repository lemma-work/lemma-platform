"""A contact's case as read back: one episode of the pod answering them, and
what happened in it, in order."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.channels.domain.entities import ContactStatus


class CaseEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    #: ``opened``, ``assigned``, ``handed_back``, ``attention``,
    #: ``acknowledged``, ``first_response``, ``status``, ``closed``,
    #: ``reopened`` or ``merged``.
    kind: str
    actor_user_id: UUID | None = None
    #: What it changed to: a status, a user id, a reason, a line number.
    value: dict[str, str | int] = {}


class Case(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    channel_id: UUID
    status: ContactStatus
    opened_at: datetime
    #: The channel's newest line when it opened: the case is the lines after.
    opened_seq: int
    assignee_user_id: UUID | None = None
    assigned_at: datetime | None = None
    attention_at: datetime | None = None
    attention_reason: str | None = None
    first_response_at: datetime | None = None
    first_response_by_user_id: UUID | None = None
    closed_at: datetime | None = None
    closed_by_user_id: UUID | None = None
    #: Oldest first; the newest ``MAX_CASE_EVENTS`` of a long case.
    events: tuple[CaseEvent, ...] = ()


#: The most events one case is read back with.
MAX_CASE_EVENTS = 200
