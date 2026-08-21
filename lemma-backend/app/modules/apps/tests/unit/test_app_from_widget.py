"""Unit tests for promoting a widget into an app (save as app)."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException

import app.modules.apps.api.controllers.app_controller as ctrl
from app.core.ports.widget_content import WidgetArtifact
from app.modules.apps.api.schemas.app_schemas import CreateAppFromWidgetRequest
from app.modules.apps.domain.entities import AppEntity
from app.modules.apps.services.app_service import AppService


def _reader(artifact):
    """A WidgetContentReader stub returning a fixed artifact."""
    return SimpleNamespace(get_widget=AsyncMock(return_value=artifact))


# --- Controller: resolve via the injected reader, then delegate to the service.


@pytest.mark.asyncio
async def test_controller_delegates_to_service():
    pod_id = uuid4()
    user_id = uuid4()
    artifact = WidgetArtifact(content="<div>chart</div>", pod_id=pod_id, title="Board")
    created = AppEntity(
        id=uuid4(), pod_id=pod_id, user_id=user_id, name="board", public_slug="board"
    )
    app_service = AsyncMock()
    app_service.create_app_from_widget.return_value = created

    data = CreateAppFromWidgetRequest(
        conversation_id=uuid4(), tool_call_id="tc_1", name="Board"
    )
    resp = await ctrl.create_app_from_widget(
        pod_id, data, app_service, _reader(artifact), SimpleNamespace(id=user_id), None
    )

    assert resp.public_slug == "board"
    _, kwargs = app_service.create_app_from_widget.call_args
    assert kwargs["artifact"] is artifact
    assert kwargs["name"] == "Board"


@pytest.mark.asyncio
async def test_missing_widget_returns_404():
    data = CreateAppFromWidgetRequest(
        conversation_id=uuid4(), tool_call_id="missing", name="X"
    )
    with pytest.raises(HTTPException) as exc:
        await ctrl.create_app_from_widget(
            uuid4(), data, AsyncMock(), _reader(None), SimpleNamespace(id=uuid4()), None
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_widget_from_other_pod_rejected():
    artifact = WidgetArtifact(content="<div>x</div>", pod_id=uuid4(), title="")
    data = CreateAppFromWidgetRequest(
        conversation_id=uuid4(), tool_call_id="tc", name="X"
    )
    with pytest.raises(HTTPException) as exc:
        await ctrl.create_app_from_widget(
            uuid4(),
            data,
            AsyncMock(),
            _reader(artifact),
            SimpleNamespace(id=uuid4()),
            None,
        )
    assert exc.value.status_code == 404


# --- Service: wraps the widget content as a single standalone index.html.


@pytest.mark.asyncio
async def test_service_uploads_single_standalone_index():
    pod_id = uuid4()
    user_id = uuid4()
    artifact = WidgetArtifact(content="<div>chart</div>", pod_id=pod_id, title="Board")
    created = AppEntity(
        id=uuid4(), pod_id=pod_id, user_id=user_id, name="board", public_slug="board"
    )

    svc = AppService(
        app_repository=AsyncMock(),
        file_manager_factory=lambda _id: AsyncMock(),
        authorization_service=AsyncMock(),
    )
    svc.create_app_with_context = AsyncMock(return_value=created)
    svc.upload_bundle = AsyncMock(return_value=created)

    result = await svc.create_app_from_widget(
        pod_id, user_id, artifact=artifact, name="Board", ctx=None
    )

    assert result is created
    _, kwargs = svc.upload_bundle.call_args
    assert kwargs["source_archive_bytes"] is None
    with ZipFile(io.BytesIO(kwargs["dist_archive_bytes"])) as z:
        names = z.namelist()
        index = z.read("index.html").decode()
    assert names == ["index.html"]
    assert index.startswith("<!doctype html>")
    assert "<div>chart</div>" in index
    assert "lemma-widget-height" not in index  # standalone, not embedded
