from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    REAL_TEAMS_THREAD_ID,
    _conversation_by_external_thread,
    _create_agent_surface,
    _ensure_connector_account,
    _load_teams_channel_mention_fixture,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e


async def test_teams_channel_surface_handles_platform_payload_and_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.platforms.teams.adapter import (
        TeamsSurfaceAdapter,
    )

    async def _fake_bot_token(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return "teams-bot-token"

    async def _disable_graph(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return None

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _fake_bot_token)
    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_graph_token", _disable_graph)
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        fake_teams.openid_config_url,
    )
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-token",
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
    )
    agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "TEAMS",
            "account_id": str(account.id),
            "allowed_channel_ids": [REAL_TEAMS_CHANNEL_ID],
        },
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/teams",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "Authorization": (
                "Bearer "
                f"{fake_teams.issue_webhook_token(audience='teams-app-id')}"
            ),
        },
    )
    assert response.status_code == 200, response.text

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[script_text("E2E agent reply [TEAMS]")],
    )
    assert isinstance(context, SurfaceChatContext)
    assert str(context.surface_id) == surface["id"]

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent["name"],
        external_thread_id=REAL_TEAMS_THREAD_ID,
    )
    assert conversation is not None
    assert conversation["metadata"]["surface_platform"] == "TEAMS"

    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=2)
    text_payloads = [
        item["body"]
        for item in teams_messages
        if item.get("body", {}).get("type") == "message"
    ]
    assert text_payloads
    assert "E2E agent reply [TEAMS]" in text_payloads[-1]["text"]


async def test_teams_oauth_token_acquisition_succeeds_via_fake_provider(
    e2e_settings,
    fake_teams,
    message_store,
    monkeypatch,
):
    """``client.py``'s real client_credentials round trip against a fake Azure
    AD token endpoint, using the ``microsoft_bot_oauth_base_url`` override."""
    from app.modules.agent_surfaces.platforms.teams import client as teams_client

    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-oauth-app-id")
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_password", "teams-oauth-secret"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_oauth_base_url", fake_teams.oauth_base_url
    )
    tenant_id = f"e2e-tenant-{uuid4().hex}"

    token = await teams_client.get_graph_token(tenant_id)

    assert token == f"fake-teams-token-{tenant_id}"
    calls = message_store.get_all("TEAMS_OAUTH_TOKEN")
    assert calls
    assert calls[-1]["tenant"] == tenant_id
    assert calls[-1]["grant_type"] == "client_credentials"
    assert calls[-1]["client_id"] == "teams-oauth-app-id"
    assert calls[-1]["scope"] == "https://graph.microsoft.com/.default"


async def test_teams_oauth_classifies_aadsts65001_as_authentication_failure(
    e2e_settings,
    fake_teams,
    message_store,
    monkeypatch,
    caplog,
):
    """A consent-not-granted (AADSTS65001) token failure is classified as an
    authentication failure (logged at ERROR, not the generic DEBUG path) and
    ``_get_token`` still fails soft -- ``None``, not an exception."""
    from app.modules.agent_surfaces.platforms.teams import client as teams_client

    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_id", "teams-oauth-app-id-65001"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_password", "teams-oauth-secret-65001"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_oauth_base_url", fake_teams.oauth_base_url
    )
    tenant_id = f"e2e-tenant-{uuid4().hex}"
    fake_teams.queue_oauth_error("AADSTS65001")

    with caplog.at_level(
        "ERROR", logger="app.modules.agent_surfaces.platforms.teams.client"
    ):
        token = await teams_client.get_graph_token(tenant_id)

    assert token is None
    assert "surface.teams.authentication_failed" in caplog.text

    # The queued failure is single-shot: the next request succeeds normally.
    retried = await teams_client.get_graph_token(tenant_id)
    assert retried == f"fake-teams-token-{tenant_id}"


async def test_teams_oauth_classifies_aadsts700016_as_authentication_failure(
    e2e_settings,
    fake_teams,
    message_store,
    monkeypatch,
    caplog,
):
    """An app-not-found-in-tenant (AADSTS700016) token failure is classified
    the same way as AADSTS65001 -- both are the bot's own auth being wrong,
    not a transient outage."""
    from app.modules.agent_surfaces.platforms.teams import client as teams_client

    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_id", "teams-oauth-app-id-700016"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_password", "teams-oauth-secret-700016"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_oauth_base_url", fake_teams.oauth_base_url
    )
    tenant_id = f"e2e-tenant-{uuid4().hex}"
    fake_teams.queue_oauth_error("AADSTS700016")

    with caplog.at_level(
        "ERROR", logger="app.modules.agent_surfaces.platforms.teams.client"
    ):
        token = await teams_client.get_graph_token(tenant_id)

    assert token is None
    assert "surface.teams.authentication_failed" in caplog.text


async def test_teams_download_attachment_resolves_via_graph_shared_link(
    e2e_settings,
    fake_teams,
    message_store,
    monkeypatch,
):
    """A Teams-shared file's ``download_url`` is redeemed through Graph's
    ``/shares/{token}/driveItem`` and then fetched from the drive item it
    resolves to -- the path every shared attachment takes, independent of
    whether its host literally ends in ``sharepoint.com``."""
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
        SurfacePlatform,
    )
    from app.modules.agent_surfaces.platforms.teams import service as teams_service

    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_id", "teams-oauth-app-id-shares"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_password", "teams-oauth-secret-shares"
    )
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_oauth_base_url", fake_teams.oauth_base_url
    )
    # service.py imports GRAPH_BASE by value from client.py, so its own
    # module-local name has to be patched -- patching client.GRAPH_BASE alone
    # would leave service.py's resolution helpers bound to the real host.
    monkeypatch.setattr(teams_service, "GRAPH_BASE", fake_teams.graph_base_url)
    tenant_id = f"e2e-tenant-{uuid4().hex}"

    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TEAMS,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="teams-thread-shares-e2e",
        message_text="",
    )
    service = teams_service.TeamsPlatformService(
        credentials={"user_data": {"tenant_id": tenant_id}}
    )
    result = await service.download_attachment_bytes(
        event,
        {
            "download_url": (
                "https://contoso.sharepoint.com/:t:/g/personal/"
                "user_contoso_com/shared-file-link"
            ),
            "content_type": "text/plain",
            "name": "shared-file.txt",
        },
    )

    assert result is not None
    content, file_name, mime_type = result
    assert content == b"fake SharePoint content drive-e2e-1/shared-item-1"
    assert file_name == "shared-file.txt"
    assert mime_type == "text/plain"

    share_calls = message_store.get_all("TEAMS_GRAPH_SHARES")
    assert share_calls
    content_calls = message_store.get_all("TEAMS_GRAPH_CONTENT")
    assert content_calls[-1]["drive_id"] == "drive-e2e-1"
    assert content_calls[-1]["item_id"] == "shared-item-1"


async def test_teams_resolves_sharepoint_site_and_document_content_url(
    e2e_settings,
    fake_teams,
    message_store,
    monkeypatch,
):
    """The ``/sites/root`` and ``/sites/{host}:{path}`` resolution chain, and
    the ``/sites/{id}/drive/root:{path}:/content`` URL it feeds -- the two
    Graph calls a real ``*.sharepoint.com`` document link takes. Exercised by
    calling ``service.py``'s resolution functions directly (a local fake
    server cannot claim a ``sharepoint.com`` hostname, which is what gates
    this path in production)."""
    import aiohttp

    from app.modules.agent_surfaces.platforms.teams import service as teams_service

    monkeypatch.setattr(teams_service, "GRAPH_BASE", fake_teams.graph_base_url)

    async with aiohttp.ClientSession() as session:
        root_site_id = await teams_service._resolve_sharepoint_site_id(
            session=session,
            token="graph-token",
            hostname="contoso.sharepoint.com",
            site_path="/",
        )
        assert root_site_id == "site-root-e2e"

        team_site_id = await teams_service._resolve_sharepoint_site_id(
            session=session,
            token="graph-token",
            hostname="contoso.sharepoint.com",
            site_path="/sites/Engineering",
        )
        assert team_site_id == "site-contoso.sharepoint.com"

        content_url = await teams_service._resolve_sharepoint_file_content_url(
            session=session,
            token="graph-token",
            url="https://contoso.sharepoint.com/sites/Engineering/Documents/report.pdf",
        )
        assert content_url == (
            f"{fake_teams.graph_base_url}/sites/site-contoso.sharepoint.com"
            "/drive/root:/Documents/report.pdf:/content"
        )

        async with session.get(
            content_url, headers={"Authorization": "Bearer graph-token"}
        ) as response:
            body = await response.read()

    assert (
        body
        == b"fake SharePoint content site-contoso.sharepoint.com:/Documents/report.pdf"
    )
    site_calls = message_store.get_all("TEAMS_GRAPH_SITES")
    assert any(call.get("path") == "root" for call in site_calls)
    assert any(
        call.get("hostname") == "contoso.sharepoint.com" for call in site_calls
    )
