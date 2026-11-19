"""Machines a person joined with ``lemma machine connect``: approve, list, remove.

See docs/architecture/connected-machines.md. The machine asks for a code
(``machine_link_controller``) and shows it; the person opens the link it
prints, sees what is asking, and approves it here -- which is where the
machine's name, its owner and any pod it is shared into are decided. The
machine then claims its secret on its own link. Removing a machine revokes its
secret and closes its link wherever it is held.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_connection import (
    ConnectionLimitReached,
    ConnectionNameTaken,
    ConnectionNotFound,
    InvalidMachineName,
    PodShareRefused,
    MachineConnection,
    MachineConnectionError,
    PairingAlreadyApproved,
    PairingNotFound,
)

router = APIRouter(tags=["Machines"])


@lru_cache(maxsize=1)
def _service():
    """Built on first use, so serving the API does not load the link's code."""
    from app.modules.workspace.services.machine_connection_service import (
        MachineConnectionService,
    )

    return MachineConnectionService()


_STATUS: tuple[tuple[type[MachineConnectionError], int], ...] = (
    (PairingNotFound, status.HTTP_404_NOT_FOUND),
    (ConnectionNotFound, status.HTTP_404_NOT_FOUND),
    (PairingAlreadyApproved, status.HTTP_409_CONFLICT),
    (ConnectionNameTaken, status.HTTP_409_CONFLICT),
    (ConnectionLimitReached, status.HTTP_403_FORBIDDEN),
    (PodShareRefused, status.HTTP_403_FORBIDDEN),
    (InvalidMachineName, status.HTTP_422_UNPROCESSABLE_CONTENT),
)


def _refused(error: MachineConnectionError) -> HTTPException:
    for kind, code in _STATUS:
        if isinstance(error, kind):
            return HTTPException(code, detail=str(error))
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(error))


def _enabled() -> None:
    if not workspace_settings.machine_connections_enabled:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Connected machines are not enabled on this server.",
        )


class MachineConnectionResponse(BaseModel):
    id: UUID
    name: str = Field(description="How refs name it: `mine:<name>`.")
    display_name: str = Field(description="What the machine called itself.")
    mode: str = Field(description="`host` (as your OS user) or `image` (Docker).")
    pod_id: UUID | None = Field(default=None, description="The pod it is shared into.")
    platform: str | None = None
    home: str | None = Field(
        default=None, description="Where Lemma's files live on the machine."
    )
    capabilities: dict[str, object] = Field(
        default_factory=dict, description="What the machine reported it can do."
    )
    runtime_version: str | None = None
    online: bool = Field(description="Whether its link is up right now.")
    last_seen_at: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def of(cls, connection: MachineConnection) -> MachineConnectionResponse:
        return cls(
            id=connection.id,
            name=connection.name,
            display_name=connection.display_name,
            mode=connection.mode,
            pod_id=connection.pod_id,
            platform=connection.platform,
            home=connection.home,
            capabilities=connection.capabilities,
            runtime_version=connection.runtime_version,
            online=connection.online,
            last_seen_at=connection.last_seen_at,
            created_at=connection.created_at,
        )


class MachineConnectionList(BaseModel):
    items: list[MachineConnectionResponse]


class PendingPairingResponse(BaseModel):
    display_name: str = Field(description="What the machine asking calls itself.")
    mode: str
    platform: str
    expires_at: datetime
    approved: bool


class ApprovePairingRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32, description="The code it shows.")
    name: str | None = Field(
        default=None,
        max_length=64,
        description="Its name in refs; defaults to one made from its own.",
    )
    pod_id: UUID | None = Field(
        default=None, description="Share it into this pod (editors and admins)."
    )


@router.get(
    "/machine-connections",
    response_model=MachineConnectionList,
    operation_id="machine_connections.list",
    summary="List your connected machines",
)
async def list_machine_connections(user: CurrentUser) -> MachineConnectionList:
    _enabled()
    items = await _service().list(user_id=user.id)
    return MachineConnectionList(items=[MachineConnectionResponse.of(i) for i in items])


@router.get(
    "/machine-connections/pairings/{code}",
    response_model=PendingPairingResponse,
    operation_id="machine_connections.pairing",
    summary="What a pairing code asks for",
)
async def get_machine_pairing(code: str, user: CurrentUser) -> PendingPairingResponse:
    _enabled()
    del user
    try:
        pending = await _service().pending(code)
    except MachineConnectionError as error:
        raise _refused(error) from None
    return PendingPairingResponse(
        display_name=pending.display_name,
        mode=pending.mode,
        platform=pending.platform,
        expires_at=pending.expires_at,
        approved=pending.approved,
    )


@router.post(
    "/machine-connections:pair",
    response_model=MachineConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="machine_connections.pair",
    summary="Approve a machine's pairing code",
)
async def pair_machine(
    body: ApprovePairingRequest, user: CurrentUser
) -> MachineConnectionResponse:
    _enabled()
    try:
        connection = await _service().approve(
            user_id=user.id, code=body.code, name=body.name, pod_id=body.pod_id
        )
    except MachineConnectionError as error:
        raise _refused(error) from None
    return MachineConnectionResponse.of(connection)


@router.delete(
    "/machine-connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="machine_connections.delete",
    summary="Disconnect a machine",
)
async def delete_machine_connection(connection_id: UUID, user: CurrentUser) -> None:
    _enabled()
    try:
        await _service().revoke(user_id=user.id, connection_id=connection_id)
    except MachineConnectionError as error:
        raise _refused(error) from None
