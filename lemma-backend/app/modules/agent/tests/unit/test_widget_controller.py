"""Unit tests for the authenticated widget serve + embed-URL mint routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.modules.agent.api.controllers.widget_controller as ctrl
from app.core.domain.errors import DomainError
from app.core.ports.widget_content import WidgetArtifact
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.services.widget_token import verify_widget_token


def _fake_service(artifact):
    class _Svc:
        def __init__(self, _session):
            pass

        async def get_widget(self, conversation_id, tool_call_id):
            return artifact

    return _Svc


def _fake_authz(ctx):
    class _Authz:
        def __init__(self, _session):
            pass

        async def build_user_context(self, **_kwargs):
            return ctx

    return _Authz


def _fake_conv_service(owner_id, pod_id):
    """A conversation service whose ownership check passes only for ``owner_id``
    in ``pod_id`` — mirrors ConversationService._validate_conversation_access."""

    class _Repo:
        async def get_conversation(self, conversation_id, **_kw):
            return SimpleNamespace(user_id=owner_id, pod_id=pod_id, agent_id=None)

    class _Svc:
        conversation_repository = _Repo()

        def _validate_conversation_access(
            self, conversation, *, user_id, pod_id, agent_id
        ):
            if (
                conversation is None
                or conversation.user_id != user_id
                or conversation.pod_id != pod_id
            ):
                raise ConversationNotFoundError()

    return lambda _uow: _Svc()


# --- serve route -----------------------------------------------------------


def test_widget_router_has_no_submit_route():
    paths = {route.path for route in ctrl.serve_router.routes}
    assert not any(path.endswith("/submit") for path in paths)


@pytest.mark.asyncio
async def test_serve_widget_with_token(monkeypatch):
    pod_id = uuid4()
    user_id = uuid4()
    artifact = WidgetArtifact(
        content='<div id="root">hi</div>', pod_id=pod_id, title="Test"
    )
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(artifact))
    monkeypatch.setattr(ctrl, "get_session", AsyncMock(return_value=None))
    monkeypatch.setattr(ctrl, "verify_widget_token", lambda _token, **_kw: user_id)
    ctx = SimpleNamespace(require=AsyncMock(return_value=None), user_id=user_id)
    monkeypatch.setattr(ctrl, "AuthorizationDataService", _fake_authz(ctx))
    monkeypatch.setattr(
        ctrl, "get_conversation_service", _fake_conv_service(user_id, pod_id)
    )

    resp = await ctrl.serve_widget(
        uuid4(), "tc_1", SimpleNamespace(), SimpleNamespace(session=None), token="tok"
    )

    assert resp.status_code == 200
    body = resp.body.decode()
    assert body.lstrip().startswith("<!doctype html>")
    assert "data-lemma-runtime-config" in body
    assert str(pod_id) in body
    assert "lemma-widget-height" in body  # embedded → height bridge
    assert '<div id="root">hi</div>' in body
    assert resp.headers["cache-control"] == "no-store"
    ctx.require.assert_awaited_once()


@pytest.mark.asyncio
async def test_serve_missing_returns_404(monkeypatch):
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(None))
    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            uuid4(), "x", SimpleNamespace(), SimpleNamespace(session=None), token=None
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_serve_unauthenticated_returns_401(monkeypatch):
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=uuid4())
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(artifact))
    monkeypatch.setattr(ctrl, "get_session", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            uuid4(), "tc", SimpleNamespace(), SimpleNamespace(session=None), token=None
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_serve_non_owner_returns_404(monkeypatch):
    """A pod member who is NOT the conversation owner must not read the widget
    even though they hold pod-level CONVERSATION_READ (the IDOR regression)."""
    pod_id = uuid4()
    viewer_id = uuid4()
    owner_id = uuid4()  # a different pod member owns the conversation
    artifact = WidgetArtifact(content="<div>secret</div>", pod_id=pod_id)
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(artifact))
    monkeypatch.setattr(ctrl, "get_session", AsyncMock(return_value=None))
    monkeypatch.setattr(ctrl, "verify_widget_token", lambda _token, **_kw: viewer_id)
    ctx = SimpleNamespace(require=AsyncMock(return_value=None), user_id=viewer_id)
    monkeypatch.setattr(ctrl, "AuthorizationDataService", _fake_authz(ctx))
    monkeypatch.setattr(
        ctrl, "get_conversation_service", _fake_conv_service(owner_id, pod_id)
    )

    with pytest.raises(HTTPException) as exc:
        await ctrl.serve_widget(
            uuid4(), "tc", SimpleNamespace(), SimpleNamespace(session=None), token="tok"
        )
    assert exc.value.status_code == 404
    # Pod-level permission passed, so the owner check is what denied access.
    ctx.require.assert_awaited_once()


@pytest.mark.asyncio
async def test_mint_non_owner_returns_404(monkeypatch):
    """Minting an embed token for another member's conversation must 404."""
    pod_id = uuid4()
    viewer_id = uuid4()
    owner_id = uuid4()
    artifact = WidgetArtifact(content="<div>secret</div>", pod_id=pod_id)
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(artifact))
    monkeypatch.setattr(
        ctrl, "get_conversation_service", _fake_conv_service(owner_id, pod_id)
    )
    ctx = SimpleNamespace(require=AsyncMock(return_value=None), user_id=viewer_id)

    with pytest.raises(HTTPException) as exc:
        await ctrl.mint_widget_embed_url(
            pod_id, uuid4(), "tc", SimpleNamespace(session=None), ctx
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_serve_non_member_returns_403(monkeypatch):
    pod_id = uuid4()
    user_id = uuid4()
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=pod_id)
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(artifact))
    monkeypatch.setattr(ctrl, "get_session", AsyncMock(return_value=None))
    monkeypatch.setattr(ctrl, "verify_widget_token", lambda _token, **_kw: user_id)
    ctx = SimpleNamespace(
        require=AsyncMock(side_effect=DomainError("denied", code="X", status_code=403)),
        user_id=user_id,
    )
    monkeypatch.setattr(ctrl, "AuthorizationDataService", _fake_authz(ctx))

    with pytest.raises(DomainError) as exc:
        await ctrl.serve_widget(
            uuid4(), "tc", SimpleNamespace(), SimpleNamespace(session=None), token="tok"
        )
    assert exc.value.status_code == 403


# --- mint route ------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_embed_url(monkeypatch):
    pod_id = uuid4()
    user_id = uuid4()
    conversation_id = uuid4()
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=pod_id)
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(artifact))
    monkeypatch.setattr(
        ctrl, "get_conversation_service", _fake_conv_service(user_id, pod_id)
    )
    ctx = SimpleNamespace(require=AsyncMock(return_value=None), user_id=user_id)

    resp = await ctrl.mint_widget_embed_url(
        pod_id, conversation_id, "tc_1", SimpleNamespace(session=None), ctx
    )

    assert f"/widgets/serve/{conversation_id}/tc_1" in resp.url
    token = parse_qs(urlparse(resp.url).query)["token"][0]
    # The minted token authenticates this exact widget for this user.
    assert (
        verify_widget_token(token, conversation_id=conversation_id, tool_call_id="tc_1")
        == user_id
    )


@pytest.mark.asyncio
async def test_mint_cross_pod_returns_404(monkeypatch):
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=uuid4())  # other pod
    monkeypatch.setattr(ctrl, "WidgetAssetService", _fake_service(artifact))
    ctx = SimpleNamespace(require=AsyncMock(), user_id=uuid4())
    with pytest.raises(HTTPException) as exc:
        await ctrl.mint_widget_embed_url(
            uuid4(), uuid4(), "tc", SimpleNamespace(session=None), ctx
        )
    assert exc.value.status_code == 404
