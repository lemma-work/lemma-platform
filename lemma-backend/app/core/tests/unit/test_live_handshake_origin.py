"""Which pages may open a cookie-authenticated live socket.

The socket reads its cookie with no anti-CSRF check, and browsers apply no
same-origin policy to a WebSocket: without an Origin allowlist any page could
open ``/pods/{pod_id}/live`` as the signed-in person.
"""

from __future__ import annotations

import pytest
from starlette.requests import HTTPConnection

from app.core.config import settings
from app.core.live.handshake_origin import cookie_socket_refusal, origin_allowed

BASE = "apps.pin.test"


@pytest.fixture(autouse=True)
def app_domain(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", BASE)


def _socket(headers: dict[str, str]) -> HTTPConnection:
    return HTTPConnection(
        {
            "type": "websocket",
            "path": "/pods/x/live",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "path_params": {},
        }
    )


def test_the_frontend_and_app_hosts_are_allowed():
    assert origin_allowed(settings.frontend_url)
    assert origin_allowed(f"https://orders.{BASE}")


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        f"https://evil.example/?x=orders.{BASE}",
        f"https://orders.{BASE}.evil.example",
        "http://[",
        "null",
    ],
)
def test_other_origins_are_refused(origin: str):
    assert not origin_allowed(origin)


@pytest.mark.parametrize("origin", ["null", "https://evil.example"])
async def test_a_cookie_socket_from_an_opaque_or_unknown_origin_is_refused(
    origin: str,
):
    assert await cookie_socket_refusal(_socket({"origin": origin})) is not None


async def test_a_cookie_socket_without_an_origin_is_not_a_browser_page():
    assert await cookie_socket_refusal(_socket({"host": "api.lemma.test"})) is None
