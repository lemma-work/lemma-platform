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


# ---------------------------------------------------------------------------
# TeamsMessageParser direct coverage. The parser is a pure function of a raw
# Bot Framework/Graph payload dict -> a parsed dataclass (or None): no DB,
# HTTP, or fake-server round trip is needed to exercise its branches, and
# testing it directly (rather than staging a full surface + webhook POST for
# every edge case) avoids repeating the ingress/DB plumbing the journeys above
# already cover for the "happy path".
# ---------------------------------------------------------------------------


def test_teams_parser_legacy_value_event_dispatch_and_fields():
    from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser

    parser = TeamsMessageParser()

    # No usable "message"/"messageUpdate" activity and no "value" list at all:
    # ``parse`` falls through both dispatch branches to its final `None`.
    assert parser.parse({}) is None
    assert parser.parse({"type": "invoke", "value": {"not": "a-list"}}) is None
    assert parser.parse({"type": "invoke", "value": ["not-a-dict"]}) is None

    # A legacy value-event activity: not a "message"/"messageUpdate" type, but
    # carrying a message-shaped dict in a top-level "value" list -- the shape
    # older Teams payloads (and some connector replay tooling) still send.
    legacy_channel_id = "19:legacy-channel-e2e@thread.tacv2"
    payload = {
        "type": "invoke",
        "serviceUrl": "https://smba.trafficmanager.net/teams",
        "tenantId": "legacy-tenant-e2e",
        "value": [
            {
                "id": "legacy-msg-1",
                "replyToId": "legacy-root-msg",
                "text": "<at>Bot</at> hello from a legacy value event",
                "from": {
                    "user": {
                        "id": "29:legacy-user",
                        "aadObjectId": "aad-legacy-user",
                        "name": "Legacy User",
                    }
                },
                "conversation": {
                    "id": legacy_channel_id,
                    "conversationType": "channel",
                },
                "channelData": {
                    "channel": {"id": legacy_channel_id},
                    "team": {"id": "team-legacy", "aadGroupId": "aad-team-legacy"},
                    "tenant": {"id": "legacy-tenant-e2e"},
                },
                "serviceUrl": "https://smba.trafficmanager.net/teams",
            }
        ],
    }

    event = parser.parse(payload)

    assert event is not None
    assert event.platform == "TEAMS"
    assert event.is_dm is False
    # ``strip_html`` removes the ``<at>...</at>`` mention tag before the
    # legacy path's own "<at>" substring check ever sees it, so this is
    # never actually detected as a mention here -- ``is_thread_reply``
    # (via ``replyToId``) is what makes the conversation start instead.
    assert event.mentioned_agent is False
    assert event.should_start_conversation is True
    assert event.tenant_id == "legacy-tenant-e2e"
    assert event.external_channel_id == legacy_channel_id
    assert event.external_thread_id == "legacy-root-msg"
    assert event.external_message_id == "legacy-msg-1"
    assert event.sender_external_user_id == "29:legacy-user"
    assert event.sender_aad_object_id == "aad-legacy-user"
    assert event.sender_display_name == "Legacy User"
    assert event.metadata["team_id"] == "team-legacy"
    assert event.metadata["team_aad_group_id"] == "aad-team-legacy"

    # An empty message body with no attachments is dropped, mirroring the
    # bot-framework-message path's own "nothing to act on" guard.
    empty_payload = dict(payload)
    empty_payload["value"] = [{**payload["value"][0], "text": ""}]
    assert parser.parse(empty_payload) is None

    # A legacy value-event message with real text but no channel/thread id to
    # route the reply to (no conversation, no channel data at all) is also
    # dropped, mirroring the bot-framework-message path's own guard.
    unroutable_payload = dict(payload)
    unroutable_payload["value"] = [
        {"id": "legacy-msg-2", "text": "hello with nowhere to route"}
    ]
    assert parser.parse(unroutable_payload) is None


def test_teams_parser_bot_framework_message_edge_cases():
    from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser

    parser = TeamsMessageParser()

    # A "message" activity with neither text nor attachments carries nothing
    # to act on.
    empty_message = {
        "type": "message",
        "from": {"id": "29:user"},
        "text": "",
        "conversation": {"id": "conv-empty", "conversationType": "personal"},
    }
    assert parser.parse(empty_message) is None

    # A personal (DM) activity whose conversation carries no id at all leaves
    # both the channel and thread id empty -- nothing to route the reply to.
    unroutable_dm = {
        "type": "message",
        "from": {"id": "29:user"},
        "text": "hello",
        "conversation": {"conversationType": "personal"},
    }
    assert parser.parse(unroutable_dm) is None

    # A file-only DM (no caption text) still has something to act on -- the
    # attachment-prompt block itself becomes the message text.
    file_only_dm = {
        "type": "message",
        "from": {"id": "29:user"},
        "text": "",
        "conversation": {"id": "conv-file-only", "conversationType": "personal"},
        "attachments": [
            {
                "name": "notes.txt",
                "contentType": "text/plain",
                "contentUrl": "https://e2e.test/notes.txt",
            }
        ],
    }
    file_only_event = parser.parse(file_only_dm)
    assert file_only_event is not None
    assert "notes.txt" in file_only_event.message_text


def test_teams_parser_parse_interaction_requires_callback_id():
    from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser

    parser = TeamsMessageParser()

    # An Adaptive Card Action.Submit whose `value` carries no
    # ``lemma_form_callback_id`` is not one of ours -- e.g. a card belonging
    # to a different bot/integration in the same tenant.
    assert parser.parse_interaction({"value": {"some_other_field": "x"}}) is None


def test_teams_parser_mentioned_bot_entity_and_legacy_fallback():
    from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser

    parser = TeamsMessageParser()

    # A non-dict entity is skipped; a non-"mention" entity is skipped; a
    # mention entity matched by name (not id) still counts.
    matched_by_name = {
        "recipient": {"id": "bot-id", "name": "LemmaBot"},
        "entities": [
            "not-a-dict",
            {"type": "otherEntity"},
            {"type": "mention", "mentioned": {"name": "LemmaBot"}},
        ],
    }
    assert parser._mentioned_bot(matched_by_name) is True

    # An entities array present but with no matching mention at all falls
    # through the loop to `False` -- distinct from the legacy no-entities path.
    no_match = {
        "recipient": {"id": "bot-id", "name": "LemmaBot"},
        "entities": [{"type": "mention", "mentioned": {"id": "someone-else"}}],
    }
    assert parser._mentioned_bot(no_match) is False

    # Legacy payload shape carries no "entities" array at all -- falls back to
    # a plain "<at>" tag presence check on the raw text.
    legacy_shape = {"text": "<at>LemmaBot</at> are you there?"}
    assert parser._mentioned_bot(legacy_shape) is True
    assert parser._mentioned_bot({"text": "no mention here"}) is False


def test_teams_parser_attachment_extraction_edge_cases():
    from app.modules.agent_surfaces.platforms.teams.parser import (
        TeamsMessageParser,
        file_type_from_url,
        filename_from_url,
    )

    parser = TeamsMessageParser()

    payload = {
        "attachments": [
            "not-a-dict",  # skipped outright
            {
                # A text/html attachment carrying an inline image and no
                # explicit name -- falls back to deriving one from the URL.
                "contentType": "text/html",
                "content": '<div itemscope="image/png"><img src="https://e2e.test/inline-a.png"></div>',
            },
        ],
        # A second inline image, only reachable via the message text itself
        # (not a text/html attachment) -- exercises the payload-level
        # fallback and its own dedup/name-from-url path.
        "text": '<p>see <img src="https://e2e.test/inline-b.png"></p>',
    }

    results = parser.extract_file_attachments(payload)

    names = {r["name"] for r in results}
    urls = {r["download_url"] for r in results}
    assert "https://e2e.test/inline-a.png" in urls
    assert "https://e2e.test/inline-b.png" in urls
    assert "inline-a.png" in names
    assert "inline-b.png" in names
    html_result = next(
        r for r in results if r["download_url"] == "https://e2e.test/inline-a.png"
    )
    assert html_result["file_type"] == "image/png"

    # filename_from_url / file_type_from_url are exercised above only via
    # attachments lacking an explicit name; cover them directly too.
    assert filename_from_url("https://e2e.test/path/report.final.pdf") == (
        "report.final.pdf"
    )
    assert filename_from_url("") is None
    assert file_type_from_url("https://e2e.test/path/report.pdf") == "pdf"
    assert file_type_from_url("https://e2e.test/no-extension") == ""


def test_teams_parser_downloadable_attachment_and_type_helpers():
    from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser

    parser = TeamsMessageParser()

    # `_looks_like_downloadable_attachment`'s three early-exit branches: no
    # download URL at all, an (unreachable-via-extract_file_attachments, but
    # independently meaningful) text/html content type, and a Bot Framework
    # card content type.
    assert parser._looks_like_downloadable_attachment({}) is False
    assert (
        parser._looks_like_downloadable_attachment(
            {"contentUrl": "https://e2e.test/x", "contentType": "text/html"}
        )
        is False
    )
    assert (
        parser._looks_like_downloadable_attachment(
            {
                "contentUrl": "https://e2e.test/x",
                "contentType": "application/vnd.microsoft.card.adaptive",
            }
        )
        is False
    )
    assert (
        parser._looks_like_downloadable_attachment(
            {"contentUrl": "https://e2e.test/x", "contentType": "application/pdf"}
        )
        is True
    )

    assert parser._extract_image_type_from_html('itemscope="image/png"') == (
        "image/png"
    )
    assert parser._extract_image_type_from_html("<p>no itemscope here</p>") == ""

    assert parser._file_type_from_name("report.PDF") == "pdf"
    assert parser._file_type_from_name("no-extension") == ""
    assert parser._file_type_from_name(None) == ""

    assert parser._file_type_from_content_type("image/png") == "png"
    assert parser._file_type_from_content_type("no-slash") == ""


def test_extract_graph_message_attachments_from_graph_channel_item():
    from app.modules.agent_surfaces.platforms.teams.parser import (
        extract_graph_message_attachments,
    )

    item = {
        "attachments": [
            {
                "contentUrl": "https://graph.e2e.test/file.pdf",
                "name": "file.pdf",
                "contentType": "application/pdf",
            },
            {"contentUrl": ""},  # filtered: no usable URL
            "not-a-dict",  # filtered: not a dict
            {
                # No name and no dotted filename in the URL -- file_type falls
                # back to deriving from the content type's subtype.
                "contentUrl": "https://graph.e2e.test/blob",
                "contentType": "application/octet-stream",
            },
        ],
        "body": {"content": '<p>see <img src="https://graph.e2e.test/inline.png"></p>'},
    }

    results = extract_graph_message_attachments(item)

    by_url = {r["download_url"]: r for r in results}
    assert by_url["https://graph.e2e.test/file.pdf"]["name"] == "file.pdf"
    assert by_url["https://graph.e2e.test/file.pdf"]["file_type"] == "pdf"
    assert by_url["https://graph.e2e.test/blob"]["file_type"] == "octet-stream"
    inline = by_url["https://graph.e2e.test/inline.png"]
    assert inline["name"] == "inline.png"
    assert inline["content_type"] == "image/*"
    # An attachment already carrying the same URL as the inline <img> is not
    # duplicated by the inline-image fallback.
    dup_item = {
        "attachments": [
            {"contentUrl": "https://graph.e2e.test/inline.png", "name": "dup.png"}
        ],
        "body": {"content": '<img src="https://graph.e2e.test/inline.png">'},
    }
    dup_results = extract_graph_message_attachments(dup_item)
    assert len(dup_results) == 1
    assert dup_results[0]["name"] == "dup.png"
