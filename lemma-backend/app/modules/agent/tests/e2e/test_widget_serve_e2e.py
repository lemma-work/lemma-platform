"""E2E: authenticated widget serving, embed-token minting, and save-as-app."""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.modules.agent.infrastructure.models import ConversationModel, MessageModel
from app.modules.test_support.e2e import fixtures as e2e_fixtures

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org

pytestmark = pytest.mark.e2e

WIDGET_CONTENT = '<div id="w">hello widget</div>'


@pytest_asyncio.fixture
async def widget_pod(authenticated_client, fixed_test_org):
    payload = {
        "name": f"Widget Test Pod {uuid4()}",
        "slug": f"widget-test-pod-{uuid4()}",
        "type": "ASSISTANT",
        "organization_id": fixed_test_org["id"],
    }
    response = await authenticated_client.post(
        "/pods", json=payload, follow_redirects=True
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _seed_widget(db_session, *, pod_id: UUID, user_id: UUID) -> tuple[UUID, str]:
    """Insert a conversation + a display_resource tool-call row; return (conv_id, tool_call_id)."""
    tool_call_id = f"tc_widget_{uuid4().hex[:8]}"
    conversation = ConversationModel(user_id=user_id, pod_id=pod_id)
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        MessageModel(
            conversation_id=conversation.id,
            sequence=0,
            role="ASSISTANT",
            kind="TOOL_CALL",
            tool_call_id=tool_call_id,
            tool_name="display_resource",
            tool_args={
                "type": "WIDGET",
                "content": WIDGET_CONTENT,
                "name": "Demo Widget",
            },
        )
    )
    await db_session.commit()
    return conversation.id, tool_call_id


def _serve_path(absolute_url: str) -> str:
    parsed = urlparse(absolute_url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


@pytest.mark.asyncio
async def test_widget_serve_requires_auth_and_injects_config(
    authenticated_client, test_app, widget_pod, fixed_test_user, db_session
):
    pod_id = UUID(widget_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    conv_id, tool_call_id = await _seed_widget(
        db_session, pod_id=pod_id, user_id=user_id
    )
    serve_path = f"/widgets/serve/{conv_id}/{tool_call_id}"

    # Member session (Bearer) → served, wrapped, config-injected, with height bridge.
    authed = await authenticated_client.get(serve_path)
    assert authed.status_code == status.HTTP_200_OK, authed.text
    body = authed.text
    assert body.lstrip().startswith("<!doctype html>")
    assert "data-lemma-runtime-config" in body
    assert str(pod_id) in body
    assert "lemma-widget-height" in body  # embedded
    assert WIDGET_CONTENT in body
    assert authed.headers["cache-control"] == "no-store"

    # No session, no token → 401 (not public, unlike app assets).
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as anon:
        anon_res = await anon.get(serve_path)
        assert anon_res.status_code == status.HTTP_401_UNAUTHORIZED, anon_res.text


@pytest.mark.asyncio
async def test_widget_embed_token_round_trip(
    authenticated_client, test_app, widget_pod, fixed_test_user, db_session
):
    pod_id = UUID(widget_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    conv_id, tool_call_id = await _seed_widget(
        db_session, pod_id=pod_id, user_id=user_id
    )

    # A member mints a signed embed URL.
    mint = await authenticated_client.post(
        f"/pods/{pod_id}/widgets/{conv_id}/{tool_call_id}/embed-token"
    )
    assert mint.status_code == status.HTTP_200_OK, mint.text
    serve_path = _serve_path(mint.json()["url"])
    assert "token=" in serve_path

    # The token authenticates the iframe document load without a session cookie.
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as anon:
        token_res = await anon.get(serve_path)
        assert token_res.status_code == status.HTTP_200_OK, token_res.text
        assert WIDGET_CONTENT in token_res.text

        # A tampered token is rejected.
        bad = await anon.get(
            f"/widgets/serve/{conv_id}/{tool_call_id}?token=not-a-token"
        )
        assert bad.status_code == status.HTTP_401_UNAUTHORIZED, bad.text


@pytest.mark.asyncio
async def test_save_widget_as_app_produces_standalone_document(
    authenticated_client, widget_pod, fixed_test_user, db_session
):
    pod_id = UUID(widget_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    conv_id, tool_call_id = await _seed_widget(
        db_session, pod_id=pod_id, user_id=user_id
    )

    app_name = f"saved_widget_{uuid4().hex[:8]}"
    promote = await authenticated_client.post(
        f"/pods/{pod_id}/apps/from-widget",
        json={
            "conversation_id": str(conv_id),
            "tool_call_id": tool_call_id,
            "name": app_name,
        },
    )
    assert promote.status_code == status.HTTP_201_CREATED, promote.text

    asset = await authenticated_client.get(f"/pods/{pod_id}/apps/{app_name}/assets")
    assert asset.status_code == status.HTTP_200_OK, asset.text
    assert WIDGET_CONTENT in asset.text
    assert "data-lemma-runtime-config" in asset.text  # config still injected
    assert "lemma-widget-height" not in asset.text  # standalone, no embed bridge
