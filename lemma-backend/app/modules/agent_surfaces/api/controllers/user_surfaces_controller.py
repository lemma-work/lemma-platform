"""User-scoped surface routes (``/surfaces/me``).

Unlike the pod-scoped ``/pods/{pod_id}/surfaces`` routes, these answer for the
*current user* across every pod they belong to — so a person reachable via a
shared system bot/number in several orgs can see all the surfaces that would
answer them and choose a default when they conflict. Also the Telegram accounts
they proved from a Lemma session (``services/telegram_identity_link``), which
hold on every bot, so belong to no one pod.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.modules.agent_surfaces.api.dependencies import UserSurfacesServiceDep
from app.modules.agent_surfaces.api.user_surface_schemas import (
    SetDefaultSurfaceRequest,
    TelegramAccountItem,
    TelegramAccountsResponse,
    UserSurfaceItem,
    UserSurfacePlatformGroup,
    UserSurfacesResponse,
)
from app.modules.agent_surfaces.services.telegram_identity_link import (
    proved_accounts,
    unlink_account,
)
from app.modules.agent_surfaces.services.user_surfaces_service import UserSurfaceGroup

router = APIRouter(prefix="/surfaces", tags=["Agent Surfaces (Me)"])


def _to_response(groups: list[UserSurfaceGroup]) -> UserSurfacesResponse:
    return UserSurfacesResponse(
        groups=[
            UserSurfacePlatformGroup(
                platform=group.platform,
                conflict=group.conflict,
                default_surface_id=group.default_surface_id,
                surfaces=[
                    UserSurfaceItem(
                        id=surface.id,
                        name=surface.name,
                        pod_id=surface.pod_id,
                        platform=surface.surface_type,
                        agent_id=surface.agent_id,
                        is_default=surface.id == group.default_surface_id,
                        shares_address=surface.id in group.contended,
                    )
                    for surface in group.surfaces
                ],
            )
            for group in groups
        ]
    )


@router.get(
    "/me",
    response_model=UserSurfacesResponse,
    operation_id="agent.surface.list_mine",
)
async def list_my_surfaces(
    user: CurrentUser,
    service: UserSurfacesServiceDep,
) -> UserSurfacesResponse:
    """Every surface across the current user's pods, grouped by platform, with
    the chosen default and a ``conflict`` flag when two of them answer at the
    same address."""
    groups = await service.list_user_surfaces(user.id)
    return _to_response(groups)


@router.put(
    "/me/default",
    response_model=UserSurfacesResponse,
    operation_id="agent.surface.set_my_default",
)
async def set_my_default_surface(
    request: SetDefaultSurfaceRequest,
    user: CurrentUser,
    service: UserSurfacesServiceDep,
) -> UserSurfacesResponse:
    """Choose which surface answers the current user for a platform when several
    could (e.g. a shared system bot spanning pods in different orgs)."""
    await service.set_default_surface(
        user_id=user.id,
        platform=request.platform,
        surface_id=request.surface_id,
    )
    groups = await service.list_user_surfaces(user.id)
    return _to_response(groups)


@router.get(
    "/me/telegram-accounts",
    response_model=TelegramAccountsResponse,
    operation_id="agent.surface.list_my_telegram_accounts",
)
async def list_my_telegram_accounts(
    user: CurrentUser, uow: UoWDep
) -> TelegramAccountsResponse:
    """The Telegram accounts the current user proved from a Lemma session, still
    theirs: every bot knows them as this user."""
    accounts = await proved_accounts(uow, user.id)
    return TelegramAccountsResponse(
        items=[
            TelegramAccountItem(
                id=account.id, name=account.name, proved_at=account.proved_at
            )
            for account in accounts
        ]
    )


@router.delete(
    "/me/telegram-accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.surface.unlink_my_telegram_account",
)
async def unlink_my_telegram_account(
    account_id: UUID, user: CurrentUser, uow: UoWDep
) -> Response:
    """Unlink one of them: from its next message the account is a stranger's."""
    if not await unlink_account(uow, user_id=user.id, account_id=account_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No such Telegram account of yours"
        )
    await uow.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
