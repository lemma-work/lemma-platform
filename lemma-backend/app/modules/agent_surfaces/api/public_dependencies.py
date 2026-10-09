"""The two things every ``/public/web`` request resolves before its handler runs.

``PublicWidgetDep`` is the widget the path's public key names: only while public
web is switched on for the deployment (``PUBLIC_WEB_ENABLED``), only while the
widget answers anybody at all, and only for a page on an origin it allows.

``PublicVisitorDep`` is who is asking: the access token in
``Authorization: Bearer``, checked against that widget and against the session
it names, which may have been revoked since the token was minted. It sets the
visitor's authorization context -- a contact, or nobody -- as the request's
current one, the way a member's request gets theirs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.authorization.anonymous import build_outsider_context
from app.core.authorization.current import set_current_context
from app.core.crypto.tokens import InvalidSignedToken
from app.core.infrastructure.db.session_uow import commit_now
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.public_web import hosted_pages_origin, public_web_enabled
from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    normalize_origin,
    refused,
)
from app.modules.agent_surfaces.services.visitor_access import (
    Visitor,
    VisitorAccess,
    read_visitor_access,
)
from app.modules.agent_surfaces.services.web_chat import WebChat
from app.modules.contacts.contracts.visitor_sessions import visitor_session_is_live
from app.modules.identity.contracts.client_address import client_ip


def origin_allowed(widget: WebWidget, origin: str) -> bool:
    """Whether a page on ``origin`` may use this widget: one it names, or the
    origin Lemma's own hosted pages run on, which every widget allows."""
    return normalize_origin(origin) == hosted_pages_origin() or widget.allows_origin(
        origin
    )


def web_chat(uow_factory: UnitOfWorkFactory = Depends(get_uow_factory)) -> WebChat:
    return WebChat(uow_factory)


def visitor_address(request: Request) -> str:
    """The browser's address, behind the proxies the deployment trusts."""
    return client_ip(request.scope)


async def public_widget(
    public_key: str, request: Request, chat: WebChat = Depends(web_chat)
) -> WebWidget:
    if not public_web_enabled():
        raise refused("Not found", 404, "not_found")
    widget = await chat.widget_for_key(public_key)
    origin = request.headers.get("origin")
    if origin and not origin_allowed(widget, origin):
        raise refused("This widget is not allowed on this site", 403, "origin")
    return widget


PublicWidgetDep = Annotated[WebWidget, Depends(public_widget)]


def _bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _access_for(token: str, widget: WebWidget) -> VisitorAccess:
    try:
        access = read_visitor_access(token)
    except InvalidSignedToken as exc:
        raise refused("Sign in to this chat again", 401, "bad_token") from exc
    if access.widget_id != widget.id or access.pod_id != widget.pod_id:
        raise refused("Sign in to this chat again", 401, "bad_token")
    return access


async def _resolve_visitor(
    request: Request,
    token: str,
    widget: WebWidget,
    session: AsyncSession,
    uow_factory: UnitOfWorkFactory,
) -> Visitor:
    access = _access_for(token, widget)
    if not await visitor_session_is_live(access.session_id, uow_factory=uow_factory):
        raise refused("This chat has ended. Start a new one.", 401, "no_session")
    context = build_outsider_context(
        session=session,
        pod_id=access.pod_id,
        organization_id=access.organization_id,
        contact_id=access.contact_id,
        actor_id=access.actor_id,
        request_id=request.headers.get("x-request-id"),
    )
    set_current_context(context)
    return Visitor(session=access, context=context)


async def _authorizer_session(uow: UoWDep) -> AsyncSession:
    """The request's session, for the visitor context's authorizer.

    Handed back before anything else runs: it holds no connection until
    something authorizes against it, so nothing slow waits with one checked
    out.
    """
    await commit_now(uow)
    return uow.session


AuthorizerSession = Annotated[AsyncSession, Depends(_authorizer_session)]


async def public_visitor(
    request: Request,
    widget: PublicWidgetDep,
    session: AuthorizerSession,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Visitor:
    token = _bearer(request)
    if token is None:
        raise refused("Sign in to this chat again", 401, "bad_token")
    return await _resolve_visitor(request, token, widget, session, uow_factory)


async def optional_visitor(
    request: Request,
    widget: PublicWidgetDep,
    session: AuthorizerSession,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Visitor | None:
    """The visitor when the request names one; a request with no token is a
    stranger, and one with a bad token is refused rather than treated as one."""
    token = _bearer(request)
    if token is None:
        return None
    return await _resolve_visitor(request, token, widget, session, uow_factory)


PublicVisitorDep = Annotated[Visitor, Depends(public_visitor)]
OptionalVisitorDep = Annotated[Visitor | None, Depends(optional_visitor)]
VisitorAddressDep = Annotated[str, Depends(visitor_address)]
