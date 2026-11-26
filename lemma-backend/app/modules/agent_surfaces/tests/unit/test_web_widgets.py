"""The two things standing between a public key and a contact's authority.

A host token is the only way a visitor is named without a code, so it is held
to its widget's secret, its widget's key, a subject and a short life. An origin
check is a courtesy, never trust -- but it must still refuse the pages it names.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import jwt
import pytest

from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    WidgetAnswer,
    mint_public_key,
    mint_secret,
)
from app.modules.agent_surfaces.services.web_visitors import verify_host_token

pytestmark = pytest.mark.unit

KEY = mint_public_key()
SECRET = mint_secret()


def _token(**claims) -> str:
    body = {"sub": "cust-1", "aud": KEY, "exp": int(time.time()) + 300, **claims}
    return jwt.encode({k: v for k, v in body.items() if v is not None}, SECRET, "HS256")


def test_a_token_from_the_customers_server_names_their_user():
    claims = verify_host_token(_token(name="Ana"), secret=SECRET, public_key=KEY)

    assert claims is not None
    assert claims.subject == "cust-1"
    assert claims.name == "Ana"


@pytest.mark.parametrize(
    "token",
    [
        jwt.encode(
            {"sub": "cust-1", "aud": KEY, "exp": int(time.time()) + 300},
            "sk_x",
            "HS256",
        ),
        _token(aud="pk_another_widget"),
        _token(exp=int(time.time()) + 3600),
        _token(exp=int(time.time()) - 120),
        _token(sub=None),
        _token(exp=None),
        "not.a.token",
    ],
    ids=[
        "other-secret",
        "other-widget",
        "long-lived",
        "expired",
        "no-subject",
        "no-expiry",
        "junk",
    ],
)
def test_anything_else_names_nobody(token):
    assert verify_host_token(token, secret=SECRET, public_key=KEY) is None


def test_an_unsigned_token_names_nobody():
    unsigned = jwt.encode(
        {"sub": "cust-1", "aud": KEY, "exp": int(time.time()) + 300},
        None,
        algorithm="none",
    )

    assert verify_host_token(unsigned, secret=SECRET, public_key=KEY) is None


def _widget(origins: tuple[str, ...]) -> WebWidget:
    return WebWidget(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="Shop chat",
        public_key=KEY,
        allowed_origins=origins,
        answer=WidgetAnswer.ANYONE,
        looked_after_by=None,
        created_at=datetime.now(timezone.utc),
    )


def test_a_widget_answers_only_the_pages_it_names():
    widget = _widget(("https://shop.example",))

    assert widget.allows_origin("https://shop.example")
    assert widget.allows_origin("https://SHOP.example/")
    assert not widget.allows_origin("https://evil.example")
    assert not widget.allows_origin(None)


def test_a_widget_naming_no_pages_may_be_embedded_anywhere():
    assert _widget(()).allows_origin("https://anywhere.example")


@pytest.mark.asyncio
async def test_anonymous_row_session_respects_rate_limit(monkeypatch):
    from unittest.mock import AsyncMock
    from app.core.public_web import public_web_settings
    from app.modules.agent_surfaces.services.web_visitors import WebVisitors
    from app.modules.agent_surfaces.domain.web_widgets import WebChatRefused

    monkeypatch.setattr(public_web_settings, "public_web_altcha_enabled", False)

    limiter = AsyncMock()
    limiter.allow_session.return_value = False

    visitors = WebVisitors(AsyncMock(), limiter=limiter)
    widget = _widget(())

    with pytest.raises(WebChatRefused) as exc:
        await visitors.open_anonymous_row_session(
            widget, altcha=None, address="1.2.3.4"
        )
    assert exc.value.status_code == 429
    assert exc.value.code == "rate_limited"


@pytest.mark.asyncio
async def test_anonymous_row_session_respects_altcha_failure(monkeypatch):
    from unittest.mock import AsyncMock
    from app.core.public_web import public_web_settings
    from app.modules.agent_surfaces.services.web_visitors import WebVisitors
    from app.modules.agent_surfaces.domain.web_widgets import WebChatRefused

    monkeypatch.setattr(public_web_settings, "public_web_altcha_enabled", True)

    limiter = AsyncMock()
    limiter.allow_session.return_value = True

    visitors = WebVisitors(AsyncMock(), limiter=limiter)
    widget = _widget(())

    with pytest.raises(WebChatRefused) as exc:
        await visitors.open_anonymous_row_session(
            widget, altcha=None, address="1.2.3.4"
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "altcha_failed"
