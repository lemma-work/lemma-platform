"""Unit tests for the authenticated widget serve + embed-URL mint routes.

Nothing here is patched. The routes take their four collaborators — the widget
reader, the conversation repository, the authorization service and the session
cookie reader — as one injected ``WidgetRouteServices`` bundle, so every test
below runs the routes' own composition: the order of the checks, the choice
between 404 and 401, and the ownership rule that the pod-level permission does
not cover.

That matters here specifically. This file used to replace those names *inside*
the controller module, and one of the stand-ins reimplemented the ownership
check; the file stayed green while the controller called a repository method
that no longer existed, because the double answered for the thing under test.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

import app.modules.agent.api.controllers.widget_controller as ctrl
from app.core.domain.errors import DomainError
from app.core.ports.widget_content import WidgetArtifact
from app.modules.agent.services.widget_token import (
    mint_widget_token,
    verify_widget_token,
)

# --- collaborators ---------------------------------------------------------


class _WidgetContent:
    """A `WidgetContentReader` holding one stored widget, or none."""

    def __init__(self, artifact: WidgetArtifact | None) -> None:
        self._artifact = artifact
        self.asked: list[tuple[UUID, str]] = []

    async def get_widget(
        self, conversation_id: UUID, tool_call_id: str
    ) -> WidgetArtifact | None:
        self.asked.append((conversation_id, tool_call_id))
        return self._artifact


class _Conversations:
    """A conversation repository holding one row owned by ``owner_id``.

    It deliberately does NOT reimplement the ownership check. It used to, and
    that copy is why this file stayed green while the controller was calling a
    method that no longer existed — the double answered for the thing under
    test. The controller calls the real `validate_conversation_access` on the
    row this hands back, so the check these tests exercise is the shipped one.
    """

    def __init__(self, *, owner_id: UUID | None, pod_id: UUID | None) -> None:
        self._owner_id = owner_id
        self._pod_id = pod_id
        self.read: list[UUID] = []

    async def get_conversation(self, conversation_id: UUID, **_kwargs):
        self.read.append(conversation_id)
        if self._owner_id is None:
            return None
        return SimpleNamespace(
            user_id=self._owner_id, pod_id=self._pod_id, agent_id=None
        )


class _UserContext:
    """The authorization decision, which belongs to `app.core.authorization`."""

    def __init__(self, user_id: UUID | None, *, denied: DomainError | None = None):
        self.user_id = user_id
        self.denied = denied
        self.required: list[tuple[str, object]] = []

    async def require(self, permission, resource) -> None:
        self.required.append((permission, resource))
        if self.denied is not None:
            raise self.denied


class _Authorization:
    """Builds `_UserContext` the way `AuthorizationDataService` builds the real one."""

    def __init__(self, context: _UserContext) -> None:
        self._context = context
        self.built: list[tuple[UUID, UUID]] = []

    async def build_user_context(self, *, user_id: UUID, pod_id: UUID) -> _UserContext:
        self.built.append((user_id, pod_id))
        return self._context


def _no_cookie():
    async def _read(_request):
        return None

    return _read


def _cookie_for(user_id: UUID | str):
    async def _read(_request):
        return SimpleNamespace(get_user_id=lambda: str(user_id))

    return _read


def _services(
    *,
    artifact: WidgetArtifact | None = None,
    owner_id: UUID | None = None,
    pod_id: UUID | None = None,
    context: _UserContext | None = None,
    read_session=None,
) -> ctrl.WidgetRouteServices:
    return ctrl.WidgetRouteServices(
        widget_content=lambda _uow: _WidgetContent(artifact),
        conversations=lambda _uow: _Conversations(owner_id=owner_id, pod_id=pod_id),
        authorization=lambda _uow: _Authorization(
            context if context is not None else _UserContext(None)
        ),
        read_session=read_session if read_session is not None else _no_cookie(),
    )


def _uow():
    """What one `uow_scope` yields: enough of a unit of work to hand around."""
    return SimpleNamespace(session=None)


def _uow_factory():
    """A factory the route can open, standing in for `get_uow_factory`.

    The serve route takes a factory rather than a live session: the SuperTokens
    lookup between its two database phases used to run with a pooled connection
    checked out.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield _uow()

    return lambda: _scope()


def _token_for(conversation_id: UUID, tool_call_id: str, user_id: UUID, *, ttl=300):
    """A real embed token, minted by the code that mints production's."""
    return mint_widget_token(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        user_id=user_id,
        expires_at_epoch=int(time.time()) + ttl,
    )


# --- serve route -----------------------------------------------------------


def test_widget_router_has_no_submit_route():
    paths = {route.path for route in ctrl.serve_router.routes}
    assert not any(path.endswith("/submit") for path in paths)


@pytest.mark.asyncio
async def test_serve_widget_with_token():
    pod_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()
    artifact = WidgetArtifact(
        content='<div id="root">hi</div>', pod_id=pod_id, title="Test"
    )
    context = _UserContext(user_id)

    resp = await ctrl.serve_widget(
        conversation_id,
        "tc_1",
        SimpleNamespace(),
        _uow_factory(),
        token=_token_for(conversation_id, "tc_1", user_id),
        services=_services(
            artifact=artifact, owner_id=user_id, pod_id=pod_id, context=context
        ),
    )

    assert resp.status_code == 200
    body = resp.body.decode()
    assert body.lstrip().startswith("<!doctype html>")
    assert "data-lemma-runtime-config" in body
    assert str(pod_id) in body
    assert "lemma-widget-height" in body  # embedded → height bridge
    assert '<div id="root">hi</div>' in body
    assert resp.headers["cache-control"] == "no-store"
    assert len(context.required) == 1


@pytest.mark.asyncio
async def test_serve_widget_with_a_session_cookie_and_no_token():
    """The cookie path: an ordinary same-site load carries no `?token=`."""
    pod_id = uuid4()
    user_id = uuid4()
    artifact = WidgetArtifact(content="<div>hi</div>", pod_id=pod_id)
    context = _UserContext(user_id)

    resp = await ctrl.serve_widget(
        uuid4(),
        "tc_1",
        SimpleNamespace(),
        _uow_factory(),
        token=None,
        services=_services(
            artifact=artifact,
            owner_id=user_id,
            pod_id=pod_id,
            context=context,
            read_session=_cookie_for(user_id),
        ),
    )

    assert resp.status_code == 200
    assert context.required == [
        (ctrl.Permissions.CONVERSATION_READ, ctrl.ResourceRef.pod(pod_id))
    ]


@pytest.mark.asyncio
async def test_serve_falls_back_to_the_token_when_the_cookie_is_unreadable():
    """An unparsable session subject must not authenticate anyone, but it also
    must not shadow a perfectly good embed token on the same request."""
    pod_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()
    artifact = WidgetArtifact(content="<div>hi</div>", pod_id=pod_id)

    resp = await ctrl.serve_widget(
        conversation_id,
        "tc_1",
        SimpleNamespace(),
        _uow_factory(),
        token=_token_for(conversation_id, "tc_1", user_id),
        services=_services(
            artifact=artifact,
            owner_id=user_id,
            pod_id=pod_id,
            context=_UserContext(user_id),
            read_session=_cookie_for("not-a-uuid"),
        ),
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_serve_missing_returns_404():
    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            uuid4(),
            "x",
            SimpleNamespace(),
            _uow_factory(),
            token=None,
            services=_services(artifact=None),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_serve_unauthenticated_returns_401():
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=uuid4())
    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            uuid4(),
            "tc",
            SimpleNamespace(),
            _uow_factory(),
            token=None,
            services=_services(artifact=artifact),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_call_id", ["tc_other", "tc"])
async def test_serve_rejects_a_token_minted_for_another_widget(tool_call_id: str):
    """The token is bound to `(conversation_id, tool_call_id, user_id)`.

    The binding used to be unreachable from these tests: `verify_widget_token`
    was replaced with a lambda that returned a user id for any string at all,
    so a token good for one widget being replayed against another was a
    property nothing here could see.
    """
    pod_id = uuid4()
    conversation_id = uuid4()
    artifact = WidgetArtifact(content="<div>secret</div>", pod_id=pod_id)
    other_conversation = conversation_id if tool_call_id != "tc" else uuid4()

    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            conversation_id,
            "tc",
            SimpleNamespace(),
            _uow_factory(),
            token=_token_for(other_conversation, tool_call_id, uuid4()),
            services=_services(artifact=artifact),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_serve_rejects_an_expired_token():
    pod_id = uuid4()
    conversation_id = uuid4()
    artifact = WidgetArtifact(content="<div>secret</div>", pod_id=pod_id)

    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            conversation_id,
            "tc",
            SimpleNamespace(),
            _uow_factory(),
            token=_token_for(conversation_id, "tc", uuid4(), ttl=-1),
            services=_services(artifact=artifact),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_serve_non_owner_returns_404():
    """A pod member who is NOT the conversation owner must not read the widget
    even though they hold pod-level CONVERSATION_READ (the IDOR regression)."""
    pod_id = uuid4()
    viewer_id = uuid4()
    owner_id = uuid4()  # a different pod member owns the conversation
    conversation_id = uuid4()
    artifact = WidgetArtifact(content="<div>secret</div>", pod_id=pod_id)
    context = _UserContext(viewer_id)

    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            conversation_id,
            "tc",
            SimpleNamespace(),
            _uow_factory(),
            token=_token_for(conversation_id, "tc", viewer_id),
            services=_services(
                artifact=artifact, owner_id=owner_id, pod_id=pod_id, context=context
            ),
        )
    assert exc.value.status_code == 404
    # Pod-level permission passed, so the owner check is what denied access.
    assert len(context.required) == 1


@pytest.mark.asyncio
async def test_serve_non_member_returns_403():
    pod_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=pod_id)
    context = _UserContext(
        user_id, denied=DomainError("denied", code="X", status_code=403)
    )

    with pytest.raises(DomainError) as exc:
        await ctrl.serve_widget(
            conversation_id,
            "tc",
            SimpleNamespace(),
            _uow_factory(),
            token=_token_for(conversation_id, "tc", user_id),
            services=_services(
                artifact=artifact, owner_id=user_id, pod_id=pod_id, context=context
            ),
        )
    assert exc.value.status_code == 403


# --- mint route ------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_embed_url():
    pod_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=pod_id)
    ctx = _UserContext(user_id)

    resp = await ctrl.mint_widget_embed_url(
        pod_id,
        conversation_id,
        "tc_1",
        _uow(),
        ctx,
        services=_services(artifact=artifact, owner_id=user_id, pod_id=pod_id),
    )

    assert f"/widgets/serve/{conversation_id}/tc_1" in resp.url
    token = parse_qs(urlparse(resp.url).query)["token"][0]
    # The minted token authenticates this exact widget for this user.
    assert (
        verify_widget_token(token, conversation_id=conversation_id, tool_call_id="tc_1")
        == user_id
    )


@pytest.mark.asyncio
async def test_mint_non_owner_returns_404():
    """Minting an embed token for another member's conversation must 404."""
    pod_id = uuid4()
    viewer_id = uuid4()
    owner_id = uuid4()
    artifact = WidgetArtifact(content="<div>secret</div>", pod_id=pod_id)
    ctx = _UserContext(viewer_id)

    with pytest.raises(HTTPException) as exc:
        await ctrl.mint_widget_embed_url(
            pod_id,
            uuid4(),
            "tc",
            _uow(),
            ctx,
            services=_services(artifact=artifact, owner_id=owner_id, pod_id=pod_id),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_mint_cross_pod_returns_404():
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=uuid4())  # other pod
    ctx = _UserContext(uuid4())

    with pytest.raises(HTTPException) as exc:
        await ctrl.mint_widget_embed_url(
            uuid4(),
            uuid4(),
            "tc",
            _uow(),
            ctx,
            services=_services(artifact=artifact),
        )
    assert exc.value.status_code == 404
    # The pod mismatch is decided before any permission is asked for, so a
    # widget in another pod cannot be probed through the authorization layer.
    assert ctx.required == []


@pytest.mark.asyncio
async def test_mint_without_an_authenticated_user_returns_401():
    pod_id = uuid4()
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=pod_id)
    ctx = _UserContext(None)

    with pytest.raises(HTTPException) as exc:
        await ctrl.mint_widget_embed_url(
            pod_id,
            uuid4(),
            "tc",
            _uow(),
            ctx,
            services=_services(artifact=artifact, pod_id=pod_id),
        )
    assert exc.value.status_code == 401
