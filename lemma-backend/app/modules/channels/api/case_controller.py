"""A contact's cases, read back: when each opened, who answered it and how
soon, how it ended, and everything that happened in it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.channels.api.access import (
    ERRORS,
    CallerDep,
    decode_cursor,
    encode_cursor,
    visible,
    wrong_kind,
)
from app.modules.channels.api.schemas import (
    AttentionResponse,
    PersonRef,
    ref,
)
from app.modules.channels.api.schemas import Names as Named
from app.modules.channels.domain.cases import Case, CaseEvent
from app.modules.channels.domain.entities import ChannelKind, ContactStatus, Principal
from app.modules.channels.infrastructure.case_history import cases_of
from app.modules.channels.infrastructure.names import Names

router = APIRouter(
    prefix="/pods/{pod_id}/channels", tags=["Channels"], responses=ERRORS
)


class CaseEventResponse(BaseModel):
    at: datetime
    kind: str = Field(
        description=(
            "`opened`, `assigned`, `handed_back`, `attention`, `acknowledged`, "
            "`first_response`, `status`, `closed`, `reopened` or `merged`."
        )
    )
    actor: PersonRef | None = Field(
        default=None, description="The member who made it, when one did."
    )
    value: dict[str, str | int] = Field(
        default_factory=dict,
        description="What it changed to: `status`, `user_id`, `reason`, `seq`, ...",
    )


class CaseResponse(BaseModel):
    id: UUID
    status: ContactStatus
    opened_at: datetime
    opened_seq: int = Field(
        description="The channel's newest line when it opened: the case is the lines after."
    )
    assignee: PersonRef | None = None
    assigned_at: datetime | None = None
    attention: AttentionResponse | None = None
    first_response_at: datetime | None = Field(
        default=None, description="The first member line to the contact in it."
    )
    first_response_by: PersonRef | None = None
    closed_at: datetime | None = None
    closed_by: PersonRef | None = None
    events: list[CaseEventResponse] = Field(
        default_factory=list,
        description="Oldest first; the newest 200 of a long case.",
    )


class ChannelCasesResponse(BaseModel):
    items: list[CaseResponse]
    next_cursor: str | None = None


@router.get(
    "/{channel_id}/cases",
    operation_id="channel.case.list",
    response_model=ChannelCasesResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_cases(
    pod_id: UUID,
    channel_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
) -> ChannelCasesResponse:
    """A contact's channel's cases, the most recently opened first, each with
    what happened in it: time to first response, reopens and time to close
    are read from here. Only a contact's channel has cases."""
    channel = await visible(uow, caller, channel_id)
    if channel.kind is not ChannelKind.CONTACT:
        raise wrong_kind("Only a contact's channel has cases")
    cases = await cases_of(
        uow.session, channel.id, limit=limit, before=decode_cursor(cursor)
    )
    names = await Names(uow).of(
        pod_id=caller.pod_id,
        people=[Principal.member(user_id) for user_id in _people(cases)],
    )
    last = cases[-1] if len(cases) == limit else None
    return ChannelCasesResponse(
        items=[_case_response(case, names) for case in cases],
        next_cursor=encode_cursor(last.opened_at, last.id) if last else None,
    )


def _people(cases: list[Case]) -> list[UUID]:
    people: list[UUID | None] = []
    for case in cases:
        people += [case.assignee_user_id, case.first_response_by_user_id]
        people += [case.closed_by_user_id]
        people += [event.actor_user_id for event in case.events]
    return [user_id for user_id in dict.fromkeys(people) if user_id is not None]


def _member(user_id: UUID | None, names: Named) -> PersonRef | None:
    return ref(Principal.member(user_id), names) if user_id is not None else None


def _event_response(event: CaseEvent, names: Named) -> CaseEventResponse:
    return CaseEventResponse(
        at=event.at,
        kind=event.kind,
        actor=_member(event.actor_user_id, names),
        value=dict(event.value),
    )


def _case_response(case: Case, names: Named) -> CaseResponse:
    return CaseResponse(
        id=case.id,
        status=case.status,
        opened_at=case.opened_at,
        opened_seq=case.opened_seq,
        assignee=_member(case.assignee_user_id, names),
        assigned_at=case.assigned_at,
        attention=AttentionResponse(
            since=case.attention_at, reason=case.attention_reason
        )
        if case.attention_at is not None
        else None,
        first_response_at=case.first_response_at,
        first_response_by=_member(case.first_response_by_user_id, names),
        closed_at=case.closed_at,
        closed_by=_member(case.closed_by_user_id, names),
        events=[_event_response(event, names) for event in case.events],
    )
