"""`/machines/{ref}/browser:access` and `:navigate`: a link to the browser, and a
page to put in front of it.

The access link is a signed grant to the machine's browser port, for a page
that embeds the live view without a Lemma session of its own. Navigation goes
through the browser relay's closed command set -- a URL, never a script.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from app.modules.workspace.api.controllers.machine_browser_view_controller import (
    browser_view_service,
)
from app.modules.workspace.api.controllers.machine_dependencies import UseMachine
from app.modules.workspace.api.controllers.machines_controller import machines_enabled
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services.browser_view_service import BrowserViewService

router = APIRouter(
    prefix="/machines/{ref}/browser",
    tags=["Machine Browser"],
    dependencies=[Depends(machines_enabled)],
)


class BrowserAccessRequest(BaseModel):
    ttl_seconds: int = Field(default=1800, ge=60, le=3600)


class BrowserAccessResponse(BaseModel):
    url: str
    expires_at: datetime


class NavigateRequest(BaseModel):
    url: HttpUrl


class NavigateResponse(BaseModel):
    url: str | None = None
    title: str | None = None


@router.post(
    ":access",
    response_model=BrowserAccessResponse,
    operation_id="machine.browser.access",
    summary="A signed link to a machine's browser",
)
async def browser_access(
    body: BrowserAccessRequest, resolved: UseMachine
) -> BrowserAccessResponse:
    # The URL handed back is a signed grant; without the key there is none.
    if not workspace_settings.runtime_credential_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Machine port access signing key is not configured",
        )
    from app.modules.workspace.services.workspace_sandbox_service import (
        WorkspaceSandboxService,
    )

    access = await WorkspaceSandboxService().create_browser_access(
        resolved.machine.id, ttl_seconds=body.ttl_seconds
    )
    return BrowserAccessResponse(url=access.url, expires_at=access.expires_at)


@router.post(
    ":navigate",
    response_model=NavigateResponse,
    operation_id="machine.browser.navigate",
    summary="Open a page in a machine's browser",
)
async def navigate(
    body: NavigateRequest,
    resolved: UseMachine,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
) -> NavigateResponse:
    try:
        landed = await service.ensure_for_sign_in(
            resolved.machine.id, origin=str(body.url), report=True
        )
    finally:
        await service.close()
    landed = landed or {}
    return NavigateResponse(
        url=str(landed.get("url")) if landed.get("url") else None,
        title=str(landed.get("title")) if landed.get("title") else None,
    )
