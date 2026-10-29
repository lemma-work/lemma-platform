"""What a group's people outside the pod may read of a table, and add to it.

``/pods/{pod_id}/channels/{channel_id}/table-grants``: a group's owners or an
admin grant it one table at a time -- the columns everybody there may read, the
columns they may fill in a new row -- and the member granting must be able to
read the table, and write it to grant adding (``datastore``'s
``channel_grants``). Anyone who can see the group can see what it was given.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.modules.channels.api.access import (
    ERRORS,
    CallerDep,
    require_manage,
    unprocessable,
    visible,
    wrong_kind,
)
from app.modules.channels.domain.entities import Channel, ChannelKind
from app.modules.datastore.contracts.channel_grants import (
    ChannelGrant,
    OpeningRefused,
    channel_grants,
    grant_table,
    revoke_table,
)

router = APIRouter(
    prefix="/pods/{pod_id}/channels", tags=["Channels"], responses=ERRORS
)


class TableGrantRequest(BaseModel):
    read_columns: list[str] = Field(
        default_factory=list,
        description="Columns everybody in the group may read.",
    )
    add_columns: list[str] = Field(
        default_factory=list,
        description="Columns they may fill in a new row; empty to only read.",
    )


class TableGrantResponse(BaseModel):
    table: str
    read_columns: list[str]
    add_columns: list[str]
    granted_by: UUID


class TableGrantsResponse(BaseModel):
    items: list[TableGrantResponse]


def _response(grant: ChannelGrant) -> TableGrantResponse:
    return TableGrantResponse(
        table=grant.table,
        read_columns=list(grant.read_columns),
        add_columns=list(grant.add_columns),
        granted_by=grant.granted_by,
    )


def _group(channel: Channel) -> Channel:
    if channel.kind is not ChannelKind.GROUP:
        raise wrong_kind("Only a group can be given a table")
    return channel


@router.get(
    "/{channel_id}/table-grants",
    operation_id="channel.table_grant.list",
    response_model=TableGrantsResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_table_grants(
    pod_id: UUID, channel_id: UUID, uow: UoWDep, caller: CallerDep
) -> TableGrantsResponse:
    """The tables the group's people outside the pod may read or add to."""
    channel = _group(await visible(uow, caller, channel_id))
    grants = await channel_grants(uow, pod_id=pod_id, channel_id=channel.id)
    return TableGrantsResponse(items=[_response(grant) for grant in grants])


@router.put(
    "/{channel_id}/table-grants/{table_name}",
    operation_id="channel.table_grant.set",
    response_model=TableGrantResponse,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def set_table_grant(
    pod_id: UUID,
    channel_id: UUID,
    table_name: str,
    body: TableGrantRequest,
    uow: UoWDep,
    caller: CallerDep,
    ctx: PodContextDep,
) -> TableGrantResponse:
    """Give the group these columns of the table to read, and those to fill.

    Everybody in the group reads what is granted -- the people outside the
    pod included -- through the granting member's own access."""
    channel = _group(await visible(uow, caller, channel_id))
    await require_manage(uow, caller, channel)
    try:
        grant = await grant_table(
            uow,
            pod_id=pod_id,
            channel_id=channel.id,
            table_name=table_name,
            read_columns=body.read_columns,
            add_columns=body.add_columns,
            ctx=ctx,
        )
    except OpeningRefused as exc:
        raise unprocessable(str(exc)) from exc
    await uow.commit()
    return _response(grant)


@router.delete(
    "/{channel_id}/table-grants/{table_name}",
    operation_id="channel.table_grant.remove",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def remove_table_grant(
    pod_id: UUID,
    channel_id: UUID,
    table_name: str,
    uow: UoWDep,
    caller: CallerDep,
    ctx: PodContextDep,
) -> Response:
    """The group no longer reads or adds to the table."""
    channel = _group(await visible(uow, caller, channel_id))
    await require_manage(uow, caller, channel)
    await revoke_table(
        uow, pod_id=pod_id, channel_id=channel.id, table_name=table_name, ctx=ctx
    )
    await uow.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
