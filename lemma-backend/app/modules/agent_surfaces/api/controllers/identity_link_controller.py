"""A member proves which Telegram account is theirs (``services/telegram_identity_link``).

Four calls, all the caller's own: mint a one-time code for one of the pod's
Telegram bots -- for somebody in a group the pod took for a stranger, when the
caller says that is them -- read where it stands while they open it in
Telegram, and confirm or decline the account it was opened from. Reading the
pod's channels is enough to ask: what is bound is the caller's own account,
and only after they confirm it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent_surfaces.api.controllers.source_controller import (
    bot_username_of,
)
from app.modules.agent_surfaces.api.dependencies import get_surface_service
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.surface_service import AgentSurfaceService
from app.modules.agent_surfaces.services.telegram_identity_link import (
    LinkCode,
    LinkState,
    LinkUnavailable,
    bind_confirmed,
    decline_link,
    link_status,
    mint_link,
    take_confirmed,
)
from app.modules.contacts.contracts import IdentityKind, contact_handles

router = APIRouter(
    prefix="/pods/{pod_id}/telegram-identity-links", tags=["Agent Surfaces"]
)


class IdentityLinkRequest(BaseModel):
    surface_id: UUID = Field(description="The pod's Telegram bot to open it with.")
    contact_id: UUID | None = Field(
        default=None,
        description=(
            "Somebody in a group the pod took for a stranger, who the caller "
            "says is them: the code then works only from that Telegram account."
        ),
    )


class IdentityLinkResponse(BaseModel):
    code: str
    url: str = Field(description="Opens a private chat with the bot, in Telegram.")
    expires_at: datetime


class TelegramAccountResponse(BaseModel):
    name: str | None
    username: str | None


class IdentityLinkStatusResponse(BaseModel):
    state: LinkState
    account: TelegramAccountResponse | None = None


@router.post(
    "",
    operation_id="telegram_identity_link.create",
    response_model=IdentityLinkResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def create_identity_link(
    pod_id: UUID,
    request: IdentityLinkRequest,
    user: CurrentUser,
    uow: UoWDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> IdentityLinkResponse:
    """A one-time code that proves, once confirmed, a Telegram account is the
    caller's. Ten minutes long, one use."""
    surface = await service.get_surface(request.surface_id)
    if surface.pod_id != pod_id or surface.surface_type is not SurfacePlatform.TELEGRAM:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such Telegram bot")
    expected = None
    if request.contact_id is not None:
        handles = await contact_handles(
            uow, pod_id=pod_id, contact_id=request.contact_id
        )
        expected = next(
            (h.value for h in handles if h.kind is IdentityKind.TELEGRAM), None
        )
        if expected is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="They have no Telegram account here"
            )
    bot_username = await bot_username_of(surface, service=service, uow=uow)
    try:
        async with connection_released(uow.session):
            code, url, expires_at = await mint_link(
                LinkCode(
                    user_id=user.id,
                    pod_id=pod_id,
                    surface_id=surface.id,
                    expected_external_user_id=expected,
                ),
                bot_username=bot_username,
            )
    except LinkUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return IdentityLinkResponse(code=code, url=url, expires_at=expires_at)


@router.get(
    "/{code}",
    operation_id="telegram_identity_link.get",
    response_model=IdentityLinkStatusResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def get_identity_link(
    pod_id: UUID, code: str, user: CurrentUser
) -> IdentityLinkStatusResponse:
    """Whether the caller's code was opened yet, and from which account."""
    found = await link_status(code, user_id=user.id)
    return IdentityLinkStatusResponse(
        state=found.state,
        account=(
            TelegramAccountResponse(
                name=found.account.name, username=found.account.username
            )
            if found.account is not None
            else None
        ),
    )


@router.post(
    "/{code}/confirm",
    operation_id="telegram_identity_link.confirm",
    response_model=TelegramAccountResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def confirm_identity_link(
    pod_id: UUID, code: str, user: CurrentUser, uow: UoWDep
) -> TelegramAccountResponse:
    """The account the code was opened from is the caller's: bind it."""
    async with connection_released(uow.session):
        confirmed = await take_confirmed(code, user_id=user.id)
    if confirmed is None or not await bind_confirmed(uow, *confirmed):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "Nothing to confirm: the link expired, was declined, or that "
                "Telegram account is already linked to somebody else."
            ),
        )
    await uow.commit()
    _, account = confirmed
    return TelegramAccountResponse(name=account.name, username=account.username)


@router.post(
    "/{code}/decline",
    operation_id="telegram_identity_link.decline",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def decline_identity_link(pod_id: UUID, code: str, user: CurrentUser) -> Response:
    """The account the code was opened from is not the caller's: spend it."""
    await decline_link(code, user_id=user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
