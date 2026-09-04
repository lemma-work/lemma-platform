"""Conversation widget serving + embed-URL minting.

A conversation widget and an app are the same primitive: a pod-authenticated
HTML page that reads ``window.__LEMMA_CONFIG__`` and uses the browser SDK. This
serves the widget's stored fragment as a full document with pod context injected
— the same serve+inject path apps use — so the frontend embeds it by URL and its
source fragment can be promoted to a standalone app unchanged.

Unlike app assets, widget HTML can carry agent-baked data, so the serve route is
**not public**: it requires a pod-member session, or a short-lived signed token
for the iframe document load when the session cookie is not sent cross-site. The
token is minted per-view by the authenticated mint endpoint. It authorizes only
that document request; browser SDK calls still require the user's normal Lemma
session.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from supertokens_python.recipe.session.interfaces import SessionContainer

from app.core.log.log import get_logger
from app.core.api.dependencies import get_uow_factory
from app.core.authorization.scope import uow_scope
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from pydantic import BaseModel
from supertokens_python.recipe.session.asyncio import get_session

from app.core.api.dependencies import UoWDep
from app.core.api.html_response import build_injected_html_response
from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.core.ports.widget_content import WidgetContentReader
from app.modules.agent.services.conversation_access import (
    validate_conversation_access,
)
from app.modules.agent.config import agent_settings
from app.core.html_document import wrap_html_fragment
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.widget_asset_service import WidgetAssetService
from app.modules.agent.services.widget_token import (
    InvalidWidgetToken,
    mint_widget_token,
    verify_widget_token,
    widget_serve_path,
)

# Self-validating serve route (session-or-token); excluded from global verify_auth.
serve_router = APIRouter(prefix="/widgets", tags=["Widgets"], redirect_slashes=False)

# Authenticated, pod-scoped mint route.
router = APIRouter(
    prefix="/pods/{pod_id}/widgets", tags=["Widgets"], redirect_slashes=False
)


logger = get_logger(__name__)


class WidgetEmbedUrlResponse(BaseModel):
    url: str


class ConversationLookup(Protocol):
    """The one repository read these routes make: the conversation's owner row."""

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        include_messages: bool = False,
        include_runs: bool = False,
    ) -> Conversation | None: ...


class UserContextBuilder(Protocol):
    """Builds the caller's authorization context for a pod."""

    async def build_user_context(self, *, user_id: UUID, pod_id: UUID) -> Context: ...


async def _read_supertokens_session(request: Request) -> SessionContainer | None:
    """The viewer's SuperTokens session, or ``None`` when the request has none."""
    return await get_session(request, session_required=False)


@dataclass(frozen=True)
class WidgetRouteServices:
    """What these two routes need from outside themselves.

    Everything below used to be reached through a module global, so the only
    way to exercise a route was to replace the name *inside* this module — and
    one of those stand-ins ended up reimplementing the ownership check, which
    is how `test_widget_controller.py` stayed green while this module called a
    repository method that no longer existed.

    Injected as one bundle rather than four parameters because FastAPI reads a
    handler's signature: each of these is `Depends`-supplied and must stay out
    of the request schema. The routes' own work — the order of the checks, the
    404-versus-401 choice, the ownership rule the pod permission does not
    cover — stays here, where a test cannot substitute for it.
    """

    #: Resolves the stored widget fragment plus its pod.
    widget_content: Callable[[SqlAlchemyUnitOfWork], WidgetContentReader] = (
        WidgetAssetService
    )
    #: Reads the conversation the widget lives in. A repository, not the whole
    #: `ConversationService`: that built an agent repository, an authorization
    #: service and a usage service per request to read one row.
    conversations: Callable[[SqlAlchemyUnitOfWork], ConversationLookup] = (
        ConversationRepository
    )
    #: Builds the caller's permission context, through the sanctioned factory
    #: rather than by reaching into ``uow.session`` here.
    authorization: Callable[[SqlAlchemyUnitOfWork], UserContextBuilder] = (
        create_authorization_data_service
    )
    #: Reads the session cookie. The SuperTokens round trip, isolated so a test
    #: can present a request with a cookie, without one, or with a broken one.
    read_session: Callable[[Request], Awaitable[SessionContainer | None]] = (
        _read_supertokens_session
    )


def widget_route_services() -> WidgetRouteServices:
    """The production collaborators, as FastAPI supplies them."""
    return WidgetRouteServices()


async def _resolve_widget_viewer(
    request: Request,
    conversation_id: UUID,
    tool_call_id: str,
    token: str | None,
    read_session: Callable[[Request], Awaitable[SessionContainer | None]],
) -> UUID | None:
    """The viewer's ``user_id`` from a session cookie, else a valid signed token."""
    try:
        session = await read_session(request)
    except Exception:
        logger.warning("agent.widget.viewer_session_unreadable.degraded", exc_info=True)
        session = None
    if session is not None:
        try:
            return UUID(session.get_user_id())
        except Exception:
            logger.warning("agent.widget.viewer_id_unparsable.degraded", exc_info=True)
    if token:
        try:
            return verify_widget_token(
                token, conversation_id=conversation_id, tool_call_id=tool_call_id
            )
        except InvalidWidgetToken:
            return None
    return None


async def _require_conversation_owner(
    uow: SqlAlchemyUnitOfWork,
    conversation_id: UUID,
    *,
    viewer_id: UUID,
    pod_id: UUID,
    conversations: Callable[[SqlAlchemyUnitOfWork], ConversationLookup],
) -> None:
    """Enforce that ``viewer_id`` owns the conversation backing this widget.

    A widget lives inside a per-user conversation, but ``CONVERSATION_READ`` is a
    pod-level permission held by every pod member — so the pod check alone lets
    any member read another member's widget HTML. Re-use the canonical ownership
    check (``conversation.user_id == viewer``) and 404 on mismatch so existence
    is not leaked.
    """
    conversation = await conversations(uow).get_conversation(conversation_id)
    try:
        validate_conversation_access(conversation, user_id=viewer_id, pod_id=pod_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Widget not found")


@serve_router.get(
    "/serve/{conversation_id}/{tool_call_id}",
    operation_id="widget.serve",
    summary="Serve Conversation Widget HTML",
    include_in_schema=False,
)
async def serve_widget(
    conversation_id: UUID,
    tool_call_id: str,
    request: Request,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    token: str | None = Query(default=None),
    services: WidgetRouteServices = Depends(widget_route_services),
) -> Response:
    # Two short scopes rather than one request-scoped session, because
    # `_resolve_widget_viewer` sits between them and is a SuperTokens round trip
    # over HTTP -- it used to run with a pooled connection checked out.
    #
    # The order is preserved exactly: lookup, then viewer, then authorization.
    # Resolving the viewer first would be tidier and would need only one scope,
    # but it turns a 404 into a 401 for an anonymous caller asking about a
    # widget that does not exist. That is arguably the better behaviour -- the
    # current order is an existence oracle for unauthenticated callers -- but it
    # is a security-semantics change, and it does not belong inside a
    # connection-scope fix.
    async with uow_scope(uow_factory) as uow:
        artifact = await services.widget_content(uow).get_widget(
            conversation_id, tool_call_id
        )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Widget not found")

    viewer_id = await _resolve_widget_viewer(
        request, conversation_id, tool_call_id, token, services.read_session
    )
    if viewer_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    async with uow_scope(uow_factory) as uow:
        ctx = await services.authorization(uow).build_user_context(
            user_id=viewer_id, pod_id=artifact.pod_id
        )
        await ctx.require(
            Permissions.CONVERSATION_READ, ResourceRef.pod(artifact.pod_id)
        )
        await _require_conversation_owner(
            uow,
            conversation_id,
            viewer_id=viewer_id,
            pod_id=artifact.pod_id,
            conversations=services.conversations,
        )

    document = wrap_html_fragment(artifact.content, title=artifact.title, embed=True)
    return build_injected_html_response(document, artifact.pod_id)


@router.post(
    "/{conversation_id}/{tool_call_id}/embed-token",
    response_model=WidgetEmbedUrlResponse,
    operation_id="widget.embed_token",
    summary="Mint Widget Embed URL",
)
async def mint_widget_embed_url(
    pod_id: UUID,
    conversation_id: UUID,
    tool_call_id: str,
    uow: UoWDep,
    ctx: PodContextDep,
    services: WidgetRouteServices = Depends(widget_route_services),
) -> WidgetEmbedUrlResponse:
    """Mint a short-lived, signed embed URL for a widget the caller may view.

    Per-view (not baked into the persisted tool result) so the token stays
    ephemeral and membership is re-checked each time the widget is opened.
    """
    artifact = await services.widget_content(uow).get_widget(
        conversation_id, tool_call_id
    )
    if artifact is None or artifact.pod_id != pod_id:
        raise HTTPException(status_code=404, detail="Widget not found")
    await ctx.require(Permissions.CONVERSATION_READ, ResourceRef.pod(pod_id))

    if ctx.user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    await _require_conversation_owner(
        uow,
        conversation_id,
        viewer_id=ctx.user_id,
        pod_id=pod_id,
        conversations=services.conversations,
    )

    expires_at = int(time.time()) + agent_settings.widget_url_expiry_seconds
    token = mint_widget_token(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        user_id=ctx.user_id,
        expires_at_epoch=expires_at,
    )
    base = settings.api_url.rstrip("/")
    path = widget_serve_path(conversation_id, tool_call_id)
    return WidgetEmbedUrlResponse(url=f"{base}{path}?token={quote(token)}")
