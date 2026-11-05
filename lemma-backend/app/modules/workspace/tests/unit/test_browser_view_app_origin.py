"""A pod app's page may not open its viewer's sandbox browser (H20).

App hosts are allowed CORS origins, so the socket's Origin check let them
through, and the browser is the person's own -- outside any pod, holding their
saved sign-ins. An app's page acts for its viewer only in its own pod.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.workspace.api.controllers import browser_view_controller as view
from app.modules.workspace.api.controllers.browser_view_session import (
    user_id_resolver,
)

APP_DOMAIN = "apps.view.test"
APP_ORIGIN = f"https://orders.{APP_DOMAIN}"


class _Service:
    def __init__(self) -> None:
        self.opened: list[str] = []

    async def open_vnc_session(
        self, user_id, *, mode, origin=None, conversation_id=None
    ):
        del mode, origin, conversation_id
        self.opened.append(str(user_id))
        return "ws://sandbox.test/vnc?mode=view", {}

    async def close(self) -> None:
        return None


async def _signed_in(_websocket) -> str:
    return str(uuid4())


def _client(service: _Service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(view.router)
    app.dependency_overrides[view.browser_view_service] = lambda: service
    # The app's origin is on the list, as it is through the CORS pattern: the
    # refusal under test is the app's, not the Origin allowlist's.
    app.dependency_overrides[view.allowed_origins] = lambda: (
        "https://app.lemma.test",
        APP_ORIGIN,
    )
    app.dependency_overrides[user_id_resolver] = lambda: _signed_in
    return TestClient(app)


@pytest.mark.parametrize("mode", [view.MODE_VIEW, view.MODE_CONTROL])
def test_an_apps_page_cannot_watch_or_drive_its_viewers_browser(
    monkeypatch, mode: str
) -> None:
    monkeypatch.setattr(settings, "app_base_domain", "apps.view.test")
    service = _Service()

    with _client(service).websocket_connect(
        f"/workspace/browser/view?mode={mode}", headers={"Origin": APP_ORIGIN}
    ) as socket:
        refusal = socket.receive()

    assert refusal["type"] == "websocket.close"
    assert refusal["code"] == view.CLOSE_ORIGIN_REFUSED
    assert service.opened == [], "no sandbox was reached for an app's page"


def test_an_app_host_that_names_no_app_is_refused_too(monkeypatch) -> None:
    """Refused without looking the app up: no app host belongs here."""
    monkeypatch.setattr(settings, "app_base_domain", "apps.view.test")
    service = _Service()
    gone = f"https://gone-{uuid4().hex[:6]}.{APP_DOMAIN}"

    client = _client(service)
    client.app.dependency_overrides[view.allowed_origins] = lambda: (gone,)
    with client.websocket_connect(
        "/workspace/browser/view", headers={"Origin": gone}
    ) as socket:
        refusal = socket.receive()

    assert refusal["code"] == view.CLOSE_ORIGIN_REFUSED
    assert service.opened == []


def test_the_workspace_itself_still_opens_it(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_base_domain", "apps.view.test")
    service = _Service()

    with _client(service).websocket_connect(
        "/workspace/browser/view", headers={"Origin": "https://app.lemma.test"}
    ) as socket:
        # The stand-in upstream does not exist, so the bridge drops; what
        # matters is that the sandbox was asked for.
        socket.receive()

    assert len(service.opened) == 1
