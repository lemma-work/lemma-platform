"""The edges of a widget's public surface that need no database.

Which origins a widget may name; how fast a key can be used, and by whom; the
access token a visitor carries; and the CORS a customer's page is answered
with -- its own origin or none, and never credentials.
"""

from __future__ import annotations

import json
import time
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.testclient import TestClient

from app.core.api.exception_handlers import register_exception_handlers

from app.core.crypto.tokens import InvalidSignedToken, mint_signed_token
from app.modules.agent_surfaces.api.public_cors import (
    MAX_BODY_BYTES,
    PublicWebCORSMiddleware,
)
from app.modules.agent_surfaces.domain.web_widgets import (
    mint_public_key,
    mint_secret,
    parse_origin,
)
from app.modules.agent_surfaces.services.outsider_limits import WebWidgetLimiter
from app.modules.agent_surfaces.services.visitor_access import (
    PURPOSE,
    read_visitor_access,
)
from app.modules.agent_surfaces.services.web_visitors import (
    sender_name,
    verify_host_token,
)

pytestmark = pytest.mark.unit

SHOP = "https://shop.example"
KEY = "pk_test"


# ---------------------------------------------------------------- origins


@pytest.mark.parametrize(
    ("typed", "origin"),
    [
        ("https://shop.example", "https://shop.example"),
        ("HTTPS://Shop.Example/", "https://shop.example"),
        ("https://shop.example:8443", "https://shop.example:8443"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://127.0.0.1", "http://127.0.0.1"),
    ],
)
def test_an_origin_is_a_scheme_and_a_host(typed, origin):
    assert parse_origin(typed) == origin


@pytest.mark.parametrize(
    "typed",
    [
        "https://shop.example/checkout",
        "https://shop.example?x=1",
        "http://shop.example",
        "http://localhost.evil.example",
        "http://127.0.0.2",
        "shop.example",
        "https://user@shop.example",
        "https://shop.example:http",
        "javascript:alert(1)",
    ],
)
def test_anything_else_is_refused(typed):
    with pytest.raises(ValueError):
        parse_origin(typed)


# ----------------------------------------------------------- host tokens


def test_a_host_user_id_too_long_to_keep_whole_names_nobody():
    key, secret = mint_public_key(), mint_secret()

    def token(subject: str) -> str:
        claims = {"sub": subject, "aud": key, "exp": int(time.time()) + 300}
        return jwt.encode(claims, secret, "HS256")

    assert verify_host_token(token("u" * 200), secret=secret, public_key=key)
    # Cutting it short would make two people one contact; it is refused instead.
    assert verify_host_token(token("u" * 201), secret=secret, public_key=key) is None


def test_a_code_email_says_who_it_is_from_without_a_header_to_break():
    assert sender_name("Acme Shop") == "Acme Shop via Lemma"
    assert "\n" not in sender_name("Acme\r\nBcc: x@y.example")
    assert "<" not in sender_name("<Acme>")
    assert sender_name("<>") == "Lemma"


# ---------------------------------------------------------- access token


def test_an_access_token_says_which_session_and_who():
    session_id, pod_id, widget_id, contact_id = uuid4(), uuid4(), uuid4(), uuid4()
    token = mint_signed_token(
        PURPOSE,
        {
            "sid": str(session_id),
            "pod": str(pod_id),
            "wid": str(widget_id),
            "cid": str(contact_id),
            "str": "CODE",
        },
        ttl_seconds=60,
    )

    access = read_visitor_access(token)

    assert (access.session_id, access.pod_id, access.widget_id) == (
        session_id,
        pod_id,
        widget_id,
    )
    assert access.actor_id == f"contact:{contact_id}"


def test_a_token_minted_for_anything_else_is_no_visitors():
    token = mint_signed_token("function-run", {"sid": str(uuid4())}, ttl_seconds=60)

    with pytest.raises(InvalidSignedToken):
        read_visitor_access(token)
    with pytest.raises(InvalidSignedToken):
        read_visitor_access(mint_signed_token(PURPOSE, {"sid": "x"}, ttl_seconds=60))


# --------------------------------------------------------------- limits


class _Counters:
    """Redis as the limiter uses it: the increment script, and DECR."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def eval(self, _script, _numkeys, key, _ttl):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def decr(self, key):
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]


async def test_one_address_hammering_a_widget_never_spends_its_day(monkeypatch):
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "surface_web_sessions_per_widget_per_day", 40)
    counters = _Counters()
    limiter = WebWidgetLimiter(redis=counters)
    widget_id = uuid4()

    allowed = [
        await limiter.allow_session(widget_id=widget_id, address="203.0.113.9")
        for _ in range(35)
    ]

    assert allowed.count(True) == 30
    # Only the thirty that were let through count against the widget's day...
    assert counters.values[f"web:sessions:{widget_id}"] == 30
    # ...and the address's window stays full, not ever-growing.
    assert counters.values["web:sessions:addr:203.0.113.9"] == 30
    assert await limiter.allow_session(widget_id=widget_id, address="198.51.100.4")


async def test_a_refusal_by_the_wider_window_gives_back_the_narrower_count(
    monkeypatch,
):
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "surface_web_sessions_per_widget_per_day", 1)
    counters = _Counters()
    limiter = WebWidgetLimiter(redis=counters)
    widget_id = uuid4()

    assert await limiter.allow_session(widget_id=widget_id, address="a")
    assert not await limiter.allow_session(widget_id=widget_id, address="b")

    assert counters.values["web:sessions:addr:b"] == 0
    assert counters.values[f"web:sessions:{widget_id}"] == 1


async def test_a_session_holds_two_streams_at_most():
    counters = _Counters()
    limiter = WebWidgetLimiter(redis=counters)
    session_id = uuid4()

    held = [await limiter.open_visitor_stream(session_id=session_id) for _ in range(3)]
    await limiter.close_visitor_stream(session_id=session_id)

    assert held == [True, True, False]
    assert await limiter.open_visitor_stream(session_id=session_id)


async def test_code_guesses_are_limited_per_session():
    limiter = WebWidgetLimiter(redis=_Counters())
    session_id = uuid4()

    tries = [
        await limiter.allow_verify(session_id=session_id, email="a@b.co", address="x")
        for _ in range(11)
    ]

    assert tries.count(True) == 10 and tries[-1] is False


# ----------------------------------------------------------------- CORS


async def _allows(public_key: str, origin: str) -> bool:
    return public_key == KEY and origin == SHOP


async def _echo(request: Request):
    body = await request.body()
    response = JSONResponse({"bytes": len(body)})
    # Whatever an inner layer says, credentials are never allowed here.
    response.headers["access-control-allow-credentials"] = "true"
    return response


async def _broken(_request: Request):
    raise RuntimeError("broken")


def _client() -> TestClient:
    """The middleware where the API has it: inside the app's own error
    handling, whose 500 is sent from outside every middleware."""
    api = FastAPI()
    register_exception_handlers(api)
    api.add_route("/public/web/{key}/session", _echo, methods=["POST"])
    api.add_route("/public/web/{key}/broken", _broken)
    api.add_route("/elsewhere", lambda _r: PlainTextResponse("app"))

    def everything_else(app):
        async def marked(scope, receive, send):
            async def send_marked(message):
                if message["type"] == "http.response.start":
                    message["headers"] = [*message["headers"], (b"x-app-cors", b"1")]
                await send(message)

            await app(scope, receive, send_marked)

        return marked

    api.add_middleware(
        PublicWebCORSMiddleware,
        everything_else=everything_else,
        origin_check=_allows,
    )
    return TestClient(api, raise_server_exceptions=False)


def _preflight(client: TestClient, origin: str, *, headers: str = "authorization"):
    return client.options(
        f"/public/web/{KEY}/session",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": headers,
        },
    )


def test_a_widgets_page_is_answered_its_preflight():
    answered = _preflight(_client(), SHOP, headers="authorization, content-type")

    assert answered.status_code == 204
    assert answered.headers["access-control-allow-origin"] == SHOP
    assert answered.headers["access-control-max-age"] == "7200"
    assert "access-control-allow-credentials" not in answered.headers


def test_another_site_and_other_headers_are_refused_a_preflight():
    client = _client()

    for refused in (
        _preflight(client, "https://evil.example"),
        _preflight(client, SHOP, headers="x-lemma-client"),
    ):
        assert refused.status_code == 400
        assert "access-control-allow-origin" not in refused.headers
        assert "access-control-allow-credentials" not in refused.headers


def test_a_response_names_the_page_only_when_the_widget_allows_it():
    client = _client()

    mine = client.post(f"/public/web/{KEY}/session", json={}, headers={"Origin": SHOP})
    theirs = client.post(
        f"/public/web/{KEY}/session",
        json={},
        headers={"Origin": "https://evil.example"},
    )

    assert mine.headers["access-control-allow-origin"] == SHOP
    assert "access-control-allow-origin" not in theirs.headers
    for response in (mine, theirs):
        assert "access-control-allow-credentials" not in response.headers


def test_a_failure_still_names_the_page_so_it_can_read_it():
    client = _client()

    broken = client.get(f"/public/web/{KEY}/broken", headers={"Origin": SHOP})
    too_big = client.post(
        f"/public/web/{KEY}/session",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Origin": SHOP, "Content-Type": "application/json"},
    )

    assert broken.status_code == 500
    assert broken.json()["code"] == "INTERNAL_ERROR"
    assert too_big.status_code == 413
    assert json.loads(too_big.text)["code"] == "too_large"
    for response in (broken, too_big):
        assert response.headers["access-control-allow-origin"] == SHOP


def test_every_other_path_keeps_the_app_wide_policy():
    elsewhere = _client().get("/elsewhere", headers={"Origin": SHOP})

    assert elsewhere.headers["x-app-cors"] == "1"
