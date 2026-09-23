"""Whether a person's computer is ready, and if not, what it is doing.

The file explorer and the browser pane each learn this only by failing: a 503
from one, a closed socket from the other. Said once, here, a page can show
"downloading the workspace" while an update's new image is fetched instead of
two panels that look broken for minutes.

Never starts anything: asking must not be what wakes a sleeping computer.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser
from app.core.log.log import get_logger
from app.modules.workspace.services.sandbox_progress import current_phase
from sandbox_runtime.errors import SandboxError

logger = get_logger(__name__)

router = APIRouter(prefix="/workspace", tags=["Workspace"])

WorkspaceState = Literal["ready", "downloading", "starting", "asleep", "unavailable"]


class WorkspaceStatusResponse(BaseModel):
    state: WorkspaceState = Field(
        description=(
            "`ready`: running. `downloading`: fetching its image, which the first "
            "start after an update does. `starting`: coming up. `asleep`: not "
            "running, and starts on first use. `unavailable`: could not be asked."
        )
    )
    detail: str | None = Field(default=None, description="A sentence for a person.")
    done_mb: int | None = Field(
        default=None, description="While `downloading`: megabytes fetched so far."
    )
    total_mb: int | None = Field(
        default=None, description="While `downloading`: megabytes in total."
    )


@router.get(
    "/status",
    response_model=WorkspaceStatusResponse,
    operation_id="workspace.status",
    summary="Whether your computer is ready",
)
async def workspace_status(user: CurrentUser) -> WorkspaceStatusResponse:
    from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
    from app.modules.workspace.services.sandbox_composition import get_sandbox_service

    service = get_sandbox_service()
    try:
        sandbox = await service.resolve(
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user.id,
        )
        phase = await current_phase(sandbox.id)
        if phase is not None:
            return WorkspaceStatusResponse(
                state=phase["phase"],
                detail=phase["detail"],
                done_mb=phase.get("done_mb"),
                total_mb=phase.get("total_mb"),
            )
        info = await service.describe(sandbox.id)
    except (SandboxError, OSError) as exc:
        logger.warning(
            "workspace.status.unavailable.degraded", error_type=type(exc).__name__
        )
        return WorkspaceStatusResponse(state="unavailable")
    if info is not None and info.status == "RUNNING":
        return WorkspaceStatusResponse(state="ready")
    return WorkspaceStatusResponse(state="asleep")
