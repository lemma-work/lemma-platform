"""A pod's web widgets: chat bubbles and forms for other people's web pages.

Every member who can read the pod sees its widgets; creating, changing and
deleting them takes what editing the pod takes. The signing secret is returned
once, when it is minted or rotated, and never again.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.modules.agent.contracts.agents import agent_id_for_name
from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    WidgetAnswer,
    WidgetKind,
    mint_public_key,
    mint_secret,
    normalize_origin,
)
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)
from app.modules.pod.contracts.members import pod_member_id

router = APIRouter(prefix="/pods/{pod_id}/web-widgets", tags=["Agent Surfaces"])

#: The pod's own assistant: its agent row shares the pod's id.
_POD_ASSISTANT = "pod_default"


class WebWidgetResponse(BaseModel):
    id: UUID
    name: str
    agent_id: UUID
    kind: WidgetKind
    public_key: str
    allowed_origins: list[str]
    answer: WidgetAnswer
    looked_after_by: UUID | None
    form_function: str | None
    form_requires_code: bool
    created_at: datetime
    embed: str = Field(description="The script tag that puts the widget on a page.")


class WebWidgetCreatedResponse(WebWidgetResponse):
    signing_secret: str = Field(
        description=(
            "Signs host tokens on the customer's server. Shown this once; keep it "
            "off web pages."
        )
    )


class WebWidgetListResponse(BaseModel):
    items: list[WebWidgetResponse]


class WebWidgetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: WidgetKind = WidgetKind.CHAT
    agent_name: str | None = Field(
        default=None,
        description="The agent that answers. The pod's assistant if omitted.",
    )
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    answer: WidgetAnswer = WidgetAnswer.ANYONE
    looked_after_by: UUID | None = None
    form_function: str | None = Field(default=None, max_length=255)
    form_requires_code: bool = False


class WebWidgetUpdateRequest(BaseModel):
    allowed_origins: list[str] | None = Field(default=None, max_length=20)
    answer: WidgetAnswer | None = None
    looked_after_by: UUID | None = None
    form_function: str | None = Field(default=None, max_length=255)
    form_requires_code: bool | None = None


class WebWidgetSecretResponse(BaseModel):
    signing_secret: str


def _embed(widget: WebWidget) -> str:
    base = str(settings.api_url).rstrip("/")
    return (
        f'<script src="{base}/public/web/widget.js" '
        f'data-lemma-key="{widget.public_key}" async></script>'
    )


def _response(widget: WebWidget) -> WebWidgetResponse:
    return WebWidgetResponse(
        id=widget.id,
        name=widget.name,
        agent_id=widget.agent_id,
        kind=widget.kind,
        public_key=widget.public_key,
        allowed_origins=list(widget.allowed_origins),
        answer=widget.answer,
        looked_after_by=widget.looked_after_by,
        form_function=widget.form_function,
        form_requires_code=widget.form_requires_code,
        created_at=widget.created_at,
        embed=_embed(widget),
    )


async def _require_member(uow: UoWDep, pod_id: UUID, user_id: UUID | None) -> None:
    if user_id is not None and await pod_member_id(uow, pod_id, user_id) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A widget must be looked after by a member of this pod",
        )


def _origins(values: list[str]) -> list[str]:
    origins = sorted({normalize_origin(value) for value in values if value.strip()})
    for origin in origins:
        if not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Origins are https:// addresses: {origin}",
            )
    return origins


@router.get(
    "",
    operation_id="agent.web_widget.list",
    response_model=WebWidgetListResponse,
    dependencies=[require_action(Permissions.POD_READ)],
)
async def list_widgets(pod_id: UUID, uow: UoWDep) -> WebWidgetListResponse:
    widgets = await WebWidgetRepository(uow.session).list(pod_id=pod_id)
    return WebWidgetListResponse(items=[_response(widget) for widget in widgets])


@router.post(
    "",
    operation_id="agent.web_widget.create",
    response_model=WebWidgetCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.POD_UPDATE)],
)
async def create_widget(
    pod_id: UUID, request: WebWidgetCreateRequest, user: CurrentUser, uow: UoWDep
) -> WebWidgetCreatedResponse:
    if request.kind is WidgetKind.FORM and not request.form_function:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A form needs a function"
        )
    looked_after_by = request.looked_after_by or user.id
    await _require_member(uow, pod_id, looked_after_by)
    agent_id = (
        await agent_id_for_name(uow.session, pod_id=pod_id, name=request.agent_name)
        if request.agent_name and request.agent_name != _POD_ASSISTANT
        else pod_id
    )
    secret = mint_secret()
    widget = await WebWidgetRepository(uow.session).create(
        pod_id=pod_id,
        agent_id=agent_id,
        name=request.name.strip(),
        kind=request.kind,
        public_key=mint_public_key(),
        secret=secret,
        allowed_origins=_origins(request.allowed_origins),
        answer=request.answer,
        looked_after_by=looked_after_by,
        form_function=request.form_function,
        form_requires_code=request.form_requires_code,
    )
    await uow.commit()
    return WebWidgetCreatedResponse(
        **_response(widget).model_dump(), signing_secret=secret
    )


@router.patch(
    "/{widget_id}",
    operation_id="agent.web_widget.update",
    response_model=WebWidgetResponse,
    dependencies=[require_action(Permissions.POD_UPDATE)],
)
async def update_widget(
    pod_id: UUID, widget_id: UUID, request: WebWidgetUpdateRequest, uow: UoWDep
) -> WebWidgetResponse:
    values: dict[str, object] = {}
    fields = request.model_fields_set
    if "allowed_origins" in fields and request.allowed_origins is not None:
        values["allowed_origins"] = _origins(request.allowed_origins)
    if "answer" in fields and request.answer is not None:
        values["answer"] = request.answer.value
    if "looked_after_by" in fields:
        await _require_member(uow, pod_id, request.looked_after_by)
        values["looked_after_by"] = request.looked_after_by
    if "form_function" in fields:
        values["form_function"] = request.form_function
    if "form_requires_code" in fields and request.form_requires_code is not None:
        values["form_requires_code"] = request.form_requires_code
    widget = await WebWidgetRepository(uow.session).update(
        pod_id=pod_id, widget_id=widget_id, values=values
    )
    if widget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Widget not found")
    await uow.commit()
    return _response(widget)


@router.post(
    "/{widget_id}/secret",
    operation_id="agent.web_widget.rotate_secret",
    response_model=WebWidgetSecretResponse,
    dependencies=[require_action(Permissions.POD_UPDATE)],
)
async def rotate_secret(
    pod_id: UUID, widget_id: UUID, uow: UoWDep
) -> WebWidgetSecretResponse:
    """Mint a new signing secret. Tokens signed with the old one stop working."""
    repository = WebWidgetRepository(uow.session)
    if await repository.get(pod_id=pod_id, widget_id=widget_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Widget not found")
    secret = mint_secret()
    await repository.rotate_secret(widget_id=widget_id, secret=secret)
    await uow.commit()
    return WebWidgetSecretResponse(signing_secret=secret)


@router.delete(
    "/{widget_id}",
    operation_id="agent.web_widget.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.POD_UPDATE)],
)
async def delete_widget(pod_id: UUID, widget_id: UUID, uow: UoWDep) -> None:
    if not await WebWidgetRepository(uow.session).delete(
        pod_id=pod_id, widget_id=widget_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Widget not found")
    await uow.commit()
