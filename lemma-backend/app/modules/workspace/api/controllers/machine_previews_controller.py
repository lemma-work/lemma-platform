"""`/machines/{ref}/previews`: open a port to people, list it, revoke it.

The preview itself is served by `machine_preview_proxy` on its own host. This
is the control half, plus ``/machine-previews/{label}:open`` -- where a
private preview sends a visitor who has no ticket yet. It runs on the API's
own host, so it knows who they are; if they may see the machine it hands them
back to the preview with a one-minute ticket.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.modules.workspace.api.controllers.machine_dependencies import (
    UowFactoryDep,
    UseMachine,
    acting_user,
    caller_for,
)
from app.modules.workspace.api.controllers.machines_controller import machines_enabled
from app.modules.workspace.domain.machine_access import MachineAccess
from app.modules.workspace.domain.machine_errors import MachineNotFound
from app.modules.workspace.services.machine_previews import (
    AUTH_PATH,
    MachinePreviews,
    Preview,
    mint_ticket,
    preview_url,
)
from app.modules.workspace.services.machine_resolver import MachineResolver

router = APIRouter(tags=["Machine Previews"], dependencies=[Depends(machines_enabled)])


class CreatePreviewRequest(BaseModel):
    port: int = Field(ge=1, le=65535)
    visibility: Literal["private", "link"] = Field(
        default="private",
        description=(
            "`private`: people who can see the machine, after signing in. "
            "`link`: anyone with the URL, for at most 24 hours."
        ),
    )
    ttl_seconds: int | None = Field(
        default=None,
        ge=60,
        description="How long it lives. Required-ish for `link`: capped at 24 hours.",
    )


class PreviewResponse(BaseModel):
    id: UUID
    port: int
    visibility: Literal["private", "link"]
    url: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class PreviewListResponse(BaseModel):
    previews: list[PreviewResponse]


def _response(preview: Preview) -> PreviewResponse:
    return PreviewResponse(
        id=preview.id,
        port=preview.port,
        visibility=preview.visibility,  # type: ignore[arg-type]
        url=preview.url,
        created_at=preview.created_at,
        expires_at=preview.expires_at,
        revoked_at=preview.revoked_at,
    )


@router.post(
    "/machines/{ref}/previews",
    response_model=PreviewResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="machine.preview.create",
    summary="Open a port on a machine as a preview URL",
)
async def create_preview(
    request: Request,
    body: CreatePreviewRequest,
    resolved: UseMachine,
    uow_factory: UowFactoryDep,
) -> PreviewResponse:
    preview = await MachinePreviews(uow_factory).create(
        resolved.machine.id,
        port=body.port,
        visibility=body.visibility,
        ttl_seconds=body.ttl_seconds,
        created_by=acting_user(request, resolved),
    )
    return _response(preview)


@router.get(
    "/machines/{ref}/previews",
    response_model=PreviewListResponse,
    operation_id="machine.preview.list",
    summary="A machine's live previews",
)
async def list_previews(
    resolved: UseMachine, uow_factory: UowFactoryDep
) -> PreviewListResponse:
    previews = await MachinePreviews(uow_factory).list(resolved.machine.id)
    return PreviewListResponse(previews=[_response(p) for p in previews])


@router.delete(
    "/machines/{ref}/previews/{preview_id}",
    response_model=PreviewResponse,
    operation_id="machine.preview.revoke",
    summary="Revoke a preview; its URL stops answering",
)
async def revoke_preview(
    preview_id: UUID, resolved: UseMachine, uow_factory: UowFactoryDep
) -> PreviewResponse:
    return _response(
        await MachinePreviews(uow_factory).revoke(resolved.machine.id, preview_id)
    )


@router.get(
    "/machine-previews/{label}:open",
    operation_id="machine.preview.open",
    summary="Open a private preview as the signed-in person",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
)
async def open_preview(
    label: str, request: Request, uow_factory: UowFactoryDep
) -> RedirectResponse:
    preview = await MachinePreviews(uow_factory).by_label(label, cached=False)
    if preview is None or not preview.live:
        raise MachineNotFound(f"preview {label}")
    async with uow_factory() as uow:
        caller = await caller_for(request, uow)
        await MachineResolver(uow).resolve(
            str(preview.sandbox_id), caller, need=MachineAccess.STATUS
        )
    assert caller.user_id is not None
    ticket = mint_ticket(label, caller.user_id)
    return RedirectResponse(
        preview_url(label, f"{AUTH_PATH}?ticket={ticket}"),
        status_code=status.HTTP_302_FOUND,
    )
