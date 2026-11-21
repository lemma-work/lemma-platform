"""A test server's known visitor code, end to end.

On a server people test pages on, the addresses they type reach no inbox, so a
page's email-code step could never be finished. With
``PUBLIC_WEB_TEST_VISITOR_CODE`` set, every code is that one and none is
mailed; a wrong guess is still refused. Production refuses the setting at
startup (``core/tests/unit/test_public_web_test_visitor_code.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.public_web import public_web_settings
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = pytest.mark.e2e

module_identity = e2e_fixtures.module_identity
fixed_test_user = e2e_fixtures.module_test_user
fixed_test_org = e2e_fixtures.module_test_org

SHOP = "https://shop.example"
TEST_CODE = "424242"


def _as(visitor: dict | None = None) -> dict[str, str]:
    token = visitor["access_token"] if visitor else ""
    return {"Origin": SHOP, "Authorization": f"Bearer {token}" if token else ""}


async def _visitor(client: AsyncClient, pod_id: str) -> tuple[str, dict]:
    created = await client.post(
        f"/pods/{pod_id}/web-widgets",
        json={"name": "Shop chat", "allowed_origins": [SHOP], "answer": "anyone"},
    )
    assert created.status_code == 201, created.text
    key = created.json()["public_key"]
    started = await client.post(f"/public/web/{key}/session", json={}, headers=_as())
    assert started.status_code == 200, started.text
    return key, started.json()


def _mail_to(email: str) -> list[Path]:
    return sorted(
        Path(settings.email_output_dir).glob(f"*{email.replace('@', '_at_')}*")
    )


async def test_a_test_server_confirms_a_visitor_with_its_known_code(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    monkeypatch.setattr(public_web_settings, "public_web_test_visitor_code", "424242")
    key, visitor = await _visitor(authenticated_client, test_pod["id"])
    email = f"tester-{key[-8:]}@nobody.example"

    sent = await authenticated_client.post(
        f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
    )
    assert sent.status_code == 200, sent.text
    assert _mail_to(email) == []

    wrong = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": "000000"},
        headers=_as(visitor),
    )
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "bad_code"

    verified = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": TEST_CODE},
        headers=_as(visitor),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["is_contact"] is True
