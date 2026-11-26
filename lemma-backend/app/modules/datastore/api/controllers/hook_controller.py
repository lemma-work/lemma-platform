"""A pod's hooks: addresses services outside it post JSON to, one row per delivery.

Making, changing, rotating and deleting a hook take changing its table;
seeing one takes reading it. The secret is in the address and is shown twice
only: when the hook is made and when it is rotated.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.core.config import settings
from app.modules.datastore.domain.hooks import (
    HOOK_PARAM,
    HOOK_SOURCE,
    KEY_PARAM,
    HookProblem,
)
from app.modules.datastore.services.hooks import (
    HookNotFound,
    HookView,
    IssuedHook,
    create_hook,
    delete_hook,
    get_hook,
    list_hooks,
    rotate_hook,
    update_hook,
)

router = APIRouter(
    prefix="/pods/{pod_id}/datastore",
    tags=["hooks"],
    redirect_slashes=False,
)

_FIELDS_DESCRIPTION = (
    "Column name to a dotted path into the posted JSON (`email`, "
    "`form_response.hidden.ref`, `answers.0.text`; `$` is the whole body). "
    "Leave it out to write the whole body to the table's `payload` JSON column. "
    "Either way `received_at` and `hook` are filled when the table has them."
)


class HookResponse(BaseModel):
    id: UUID
    name: str
    table: str
    enabled: bool
    fields: dict[str, str] | None = Field(description=_FIELDS_DESCRIPTION)
    created_by: UUID = Field(description="The member each row is added as.")
    created_at: datetime
    last_received_at: datetime | None = Field(
        description="When a delivery last added a row."
    )
    last_error: str | None = Field(
        description=(
            "Why the last refused delivery was refused, as its sender was told, "
            "or that a duplicate added no row. Cleared when a row next arrives."
        )
    )
    last_error_at: datetime | None


class IssuedHookResponse(HookResponse):
    secret: str = Field(description="Shown now and never again.")
    url: str = Field(
        description="The address to give the service. It carries the secret."
    )


class HooksResponse(BaseModel):
    items: list[HookResponse]


class CreateHookRequest(BaseModel):
    name: str = Field(description="Lowercase letters, digits, '-' or '_'.")
    table: str = Field(description="The table each delivery adds a row to.")
    fields: dict[str, str] | None = Field(default=None, description=_FIELDS_DESCRIPTION)


class UpdateHookRequest(BaseModel):
    enabled: bool | None = None
    fields: dict[str, str] | None = Field(
        default=None,
        description=_FIELDS_DESCRIPTION + " Send null to go back to the whole body.",
    )


def _hook(view: HookView) -> HookResponse:
    return HookResponse(
        id=view.id,
        name=view.name,
        table=view.table,
        enabled=view.enabled,
        fields=view.fields,
        created_by=view.created_by,
        created_at=view.created_at,
        last_received_at=view.last_received_at,
        last_error=view.last_error,
        last_error_at=view.last_error_at,
    )


def hook_url(hook_id: UUID, secret: str) -> str:
    query = urlencode({HOOK_PARAM: str(hook_id), KEY_PARAM: secret})
    return f"{settings.api_url.rstrip('/')}/webhooks/{HOOK_SOURCE}?{query}"


def _issued(issued: IssuedHook) -> IssuedHookResponse:
    return IssuedHookResponse(
        **_hook(issued.hook).model_dump(),
        secret=issued.secret,
        url=hook_url(issued.hook.id, issued.secret),
    )


def _refused(problem: HookProblem) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "HOOK_REFUSED", "message": str(problem)},
    )


def _missing(name: str) -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND,
        detail={"code": "HOOK_NOT_FOUND", "message": f"No hook named '{name}'"},
    )


@router.post(
    "/hooks",
    response_model=IssuedHookResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="hook.create",
    summary="Make A Hook",
    description=(
        "Make an address a service outside the pod -- a form tool, Zapier, "
        "Make -- can post JSON to. Each delivery adds a row to the table, as "
        "the member making the hook, who must be able to change the table. "
        "The response carries the secret and the address; neither is shown "
        "again."
    ),
)
async def post_hook(
    pod_id: UUID, request: CreateHookRequest, uow: UoWDep, ctx: PodContextDep
) -> IssuedHookResponse:
    try:
        issued = await create_hook(
            uow,
            pod_id=pod_id,
            name=request.name,
            table_name=request.table,
            fields=request.fields,
            ctx=ctx,
        )
    except HookProblem as exc:
        raise _refused(exc) from exc
    await uow.commit()
    return _issued(issued)


@router.get(
    "/hooks",
    response_model=HooksResponse,
    operation_id="hook.list",
    summary="List Hooks",
    description="The pod's hooks on tables the caller can read.",
)
async def get_hooks(pod_id: UUID, uow: UoWDep, ctx: PodContextDep) -> HooksResponse:
    return HooksResponse(
        items=[_hook(view) for view in await list_hooks(uow, pod_id=pod_id, ctx=ctx)]
    )


@router.get(
    "/hooks/{name}",
    response_model=HookResponse,
    operation_id="hook.get",
    summary="Get A Hook",
)
async def get_one_hook(
    pod_id: UUID, name: str, uow: UoWDep, ctx: PodContextDep
) -> HookResponse:
    try:
        return _hook(await get_hook(uow, pod_id=pod_id, name=name, ctx=ctx))
    except HookNotFound as exc:
        raise _missing(name) from exc


@router.patch(
    "/hooks/{name}",
    response_model=HookResponse,
    operation_id="hook.update",
    summary="Change A Hook",
    description="Switch a hook on or off, or change what a delivery becomes.",
)
async def patch_hook(
    pod_id: UUID,
    name: str,
    request: UpdateHookRequest,
    uow: UoWDep,
    ctx: PodContextDep,
) -> HookResponse:
    try:
        view = await update_hook(
            uow,
            pod_id=pod_id,
            name=name,
            ctx=ctx,
            enabled=request.enabled,
            fields=request.fields,
            set_fields="fields" in request.model_fields_set,
        )
    except HookNotFound as exc:
        raise _missing(name) from exc
    except HookProblem as exc:
        raise _refused(exc) from exc
    await uow.commit()
    return _hook(view)


@router.post(
    "/hooks/{name}/rotate",
    response_model=IssuedHookResponse,
    operation_id="hook.rotate",
    summary="Give A Hook A New Secret",
    description=(
        "The old address stops working at once. The response carries the new "
        "secret and address; neither is shown again."
    ),
)
async def post_rotate_hook(
    pod_id: UUID, name: str, uow: UoWDep, ctx: PodContextDep
) -> IssuedHookResponse:
    try:
        issued = await rotate_hook(uow, pod_id=pod_id, name=name, ctx=ctx)
    except HookNotFound as exc:
        raise _missing(name) from exc
    except HookProblem as exc:
        raise _refused(exc) from exc
    await uow.commit()
    return _issued(issued)


@router.delete(
    "/hooks/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="hook.delete",
    summary="Delete A Hook",
)
async def delete_one_hook(
    pod_id: UUID, name: str, uow: UoWDep, ctx: PodContextDep
) -> None:
    try:
        await delete_hook(uow, pod_id=pod_id, name=name, ctx=ctx)
    except HookNotFound as exc:
        raise _missing(name) from exc
    except HookProblem as exc:
        raise _refused(exc) from exc
    await uow.commit()
