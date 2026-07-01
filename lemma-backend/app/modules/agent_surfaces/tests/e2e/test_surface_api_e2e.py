from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _create_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
)

pytestmark = pytest.mark.e2e


async def test_surface_http_lifecycle_openapi_and_no_per_surface_webhook(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-surface-crud",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent = await _create_agent(authenticated_client, pod_id)
    # Surfaces are created via POST; name defaults to the lowercased platform.
    created = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "default_agent_name": agent["name"],
            "account_id": str(account.id),
        },
    )
    assert created.status_code == 200, created.text
    surface = created.json()
    assert surface["name"] == "slack"
    assert surface["agent_name"] == agent["name"]
    assert surface["uses_default_agent"] is False
    assert surface["webhook_url"].endswith("/surfaces/webhooks/slack")

    default_agent_surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM"},
    )
    assert default_agent_surface["agent_id"] is None
    assert default_agent_surface["uses_default_agent"] is True

    listed = await authenticated_client.get(f"/pods/{pod_id}/surfaces")
    assert listed.status_code == 200, listed.text
    listed_ids = {item["id"] for item in listed.json()["items"]}
    assert {surface["id"], default_agent_surface["id"]}.issubset(listed_ids)

    # Listing can be filtered by platform.
    slack_only = await authenticated_client.get(
        f"/pods/{pod_id}/surfaces", params={"platform": "SLACK"}
    )
    assert slack_only.status_code == 200, slack_only.text
    assert {item["id"] for item in slack_only.json()["items"]} == {surface["id"]}

    # Surfaces are addressed by their stable name, not the platform.
    fetched = await authenticated_client.get(f"/pods/{pod_id}/surfaces/slack")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["agent_name"] == agent["name"]

    rejected_old_shape = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={
            "mode": "CHANNEL",
            "external_channel_id": "C123",
            "routing_scope": "PERSONAL",
        },
    )
    assert rejected_old_shape.status_code == 422, rejected_old_shape.text

    reassigned_agent = await _create_agent(authenticated_client, pod_id)
    reassigned = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"default_agent_name": reassigned_agent["name"]},
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["agent_id"] == reassigned_agent["id"]
    assert reassigned.json()["agent_name"] == reassigned_agent["name"]
    assert reassigned.json()["uses_default_agent"] is False

    reset_to_default = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"default_agent_name": None},
    )
    assert reset_to_default.status_code == 200, reset_to_default.text
    assert reset_to_default.json()["agent_id"] is None
    assert reset_to_default.json()["uses_default_agent"] is True

    # Disable rides on the same PATCH via is_enabled (distinct from delete).
    disabled = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"is_enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "INACTIVE"

    removed_route = await authenticated_client.get(
        f"/pods/{pod_id}/surfaces/{surface['id']}/webhook-url"
    )
    assert removed_route.status_code == 404
    removed_ingress = await authenticated_client.get(
        f"/surfaces/webhooks/surface/{surface['id']}"
    )
    assert removed_ingress.status_code == 404

    # The per-surface setup read merges live state with the static platform guide.
    # This Slack surface uses SYSTEM credentials (Lemma's own app), so there is
    # nothing for the user to configure: ready, no actions.
    setup = await authenticated_client.get(f"/pods/{pod_id}/surfaces/slack/setup")
    assert setup.status_code == 200, setup.text
    setup_body = setup.json()
    assert setup_body["platform"] == "SLACK"
    assert setup_body["exists"] is True
    assert setup_body["status"] == "INACTIVE"
    assert setup_body["ready"] is True
    assert setup_body["actions"] == []
    assert setup_body["webhook_url"].endswith("/surfaces/webhooks/slack")
    assert setup_body["guide"]["platform"] == "SLACK"

    # The pre-creation guide works with no surface (platform-level, not
    # surface-scoped) and needs no `exists`/live-state fields.
    teams_guide = await authenticated_client.get(
        f"/pods/{pod_id}/surface-setup/teams"
    )
    assert teams_guide.status_code == 200, teams_guide.text
    assert teams_guide.json()["platform"] == "TEAMS"
    # And is a 404 on the per-surface endpoint until a Teams surface exists.
    missing_teams_setup = await authenticated_client.get(
        f"/pods/{pod_id}/surfaces/teams/setup"
    )
    assert missing_teams_setup.status_code == 404

    openapi = await authenticated_client.get("/openapi.json")
    assert openapi.status_code == 200, openapi.text
    openapi_schema = openapi.json()
    paths = openapi_schema["paths"]
    assert (
        paths["/pods/{pod_id}/surfaces"]["post"]["operationId"]
        == "agent.surface.create"
    )
    assert (
        paths["/pods/{pod_id}/surfaces/{surface_name}"]["patch"]["operationId"]
        == "agent.surface.update"
    )
    assert (
        paths["/pods/{pod_id}/surfaces/{surface_name}"]["delete"]["operationId"]
        == "agent.surface.delete"
    )
    assert (
        paths["/pods/{pod_id}/surfaces/{surface_name}/setup"]["get"]["operationId"]
        == "agent.surface.setup"
    )
    assert (
        paths["/pods/{pod_id}/surface-setup/{platform}"]["get"]["operationId"]
        == "agent.surface.setup_guide"
    )
    assert (
        paths["/surfaces/webhooks/{platform}"]["post"]["operationId"]
        == "surface.webhook.handle_platform"
    )
    surface_openapi = json.dumps(
        {
            "paths": {
                key: value
                for key, value in openapi_schema["paths"].items()
                if "/surfaces" in key
            },
            "schemas": {
                key: value
                for key, value in openapi_schema.get("components", {})
                .get("schemas", {})
                .items()
                if "Surface" in key
                or key in ("SurfaceCreateRequest", "SurfaceUpdateRequest")
            },
        },
        sort_keys=True,
    )
    request_schema_names = {"SurfaceCreateRequest", "SurfaceUpdateRequest"}
    response_schema_names = {"AgentSurfaceResponse"}
    public_surface_properties = {}
    for schema_name in request_schema_names | response_schema_names:
        schema = openapi_schema["components"]["schemas"][schema_name]
        public_surface_properties[schema_name] = set(schema.get("properties", {}))
    forbidden_fields = {
        "mode",
        "event_mode",
        "delivery_mode",
        "routing_scope",
        "external_workspace_id",
        "external_tenant_id",
        "external_channel_id",
        "is_active",
        "surface_type",
        "default_agent_id",
    }
    for schema_name, properties in public_surface_properties.items():
        assert properties.isdisjoint(forbidden_fields), schema_name
    # The old collapsed upsert and any never-shipped ops must be gone from the spec.
    for removed_name in (
        "assistant_name",
        "uses_pod_assistant",
        "assistant_id",
        "AssistantSurface",
        "webhook_mode",
        "/surfaces/webhooks/surface",
        "agent.surface.upsert",
        "agent.surface.toggle",
        "agent.surface.update_channels",
        "agent.surface.admin_consent_info",
        "agent.surface.platform_checklist",
        "SurfaceUpsertRequest",
    ):
        assert removed_name not in surface_openapi


async def test_surface_config_round_trips_and_supports_partial_updates(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    """The config the API returns mirrors exactly what callers send:
    {identity, channels, dm_conversation_reset_after_hours} — no derived or
    internal fields, and partial upserts leave unsent fields untouched."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-config-roundtrip",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    route_agent = await _create_agent(authenticated_client, pod_id)

    created = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "account_id": str(account.id),
            "config": {
                "identity": {"allowed_domains": ["Lemma.Test "]},
                "channels": [
                    {"channel_id": "C-ROUTED", "agent_name": route_agent["name"]}
                ],
                "dm_conversation_reset_after_hours": 6,
            },
        },
    )
    assert created.status_code == 200, created.text
    config = created.json()["config"]
    # Response config carries exactly the user-editable fields, nothing else.
    assert set(config) == {
        "identity",
        "channels",
        "dm_conversation_reset_after_hours",
        "send_policy",
    }
    assert config["dm_conversation_reset_after_hours"] == 6
    # Identity values are normalized on write.
    assert config["identity"]["allowed_domains"] == ["lemma.test"]
    route = config["channels"][0]
    # Routes mirror the input exactly: agent referenced by name, presence means active.
    # Channels are always mention-gated (no per-route requires_mention toggle).
    assert set(route) == {"channel_id", "channel_name", "agent_name"}
    assert route["channel_id"] == "C-ROUTED"
    assert route["agent_name"] == route_agent["name"]

    # A partial update (only one config field) leaves identity + channels intact.
    partial = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"dm_conversation_reset_after_hours": 48}},
    )
    assert partial.status_code == 200, partial.text
    config = partial.json()["config"]
    assert config["dm_conversation_reset_after_hours"] == 48
    assert config["identity"]["allowed_domains"] == ["lemma.test"]
    assert config["channels"][0]["channel_id"] == "C-ROUTED"

    openapi = await authenticated_client.get("/openapi.json")
    schemas = openapi.json()["components"]["schemas"]
    assert set(schemas["SurfaceConfigResponse"]["properties"]) == {
        "identity",
        "channels",
        "dm_conversation_reset_after_hours",
        "send_policy",
    }
    assert set(schemas["SurfaceBehaviorConfigInput"]["properties"]) == {
        "identity",
        "channels",
        "dm_conversation_reset_after_hours",
        "send_policy",
    }


async def test_delete_surface_removes_row_provider_webhook_and_releases_account(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={"bot_token": "telegram-delete-token"},
    )

    created = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={"platform": "TELEGRAM", "account_id": str(account.id)},
    )
    assert created.status_code == 200, created.text
    surface = created.json()
    assert surface["webhook_url"] == (
        f"https://api.example.test/surfaces/{surface['id']}/webhook"
    )

    deleted = await authenticated_client.delete(
        f"/pods/{pod_id}/surfaces/telegram"
    )
    assert deleted.status_code == 204, deleted.text

    fetched = await authenticated_client.get(f"/pods/{pod_id}/surfaces/telegram")
    assert fetched.status_code == 404

    webhook_calls = message_store.get_all("TELEGRAM_WEBHOOK")
    # Registration is idempotent (deleteWebhook then setWebhook), and deleting
    # the surface tears the webhook down.
    assert [call["method"] for call in webhook_calls] == [
        "deleteWebhook",
        "setWebhook",
        "deleteWebhook",
    ]
    assert webhook_calls[0]["body"] == {"drop_pending_updates": True}
    assert webhook_calls[1]["body"]["url"] == surface["webhook_url"]
    assert webhook_calls[2]["body"] == {"drop_pending_updates": False}

    # Deleting frees the account for a fresh surface (new id).
    recreated = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={"platform": "TELEGRAM", "account_id": str(account.id)},
    )
    assert recreated.status_code == 200, recreated.text
    assert recreated.json()["id"] != surface["id"]


async def test_upsert_preserves_channel_routes_and_explicit_credential_mode(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-system-install",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    created = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "account_id": str(account.id),
            "credential_mode": "SYSTEM",
            "config": {"identity": {"allowed_domains": ["lemma.test"]}},
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["credential_mode"] == "SYSTEM"
    assert created.json()["config"]["identity"]["allowed_domains"] == ["lemma.test"]

    # Channel routes are just another config field on the same PATCH.
    routed = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={
            "config": {
                "channels": [
                    {
                        "channel_id": "C123",
                        "channel_name": "support",
                    }
                ]
            }
        },
    )
    assert routed.status_code == 200, routed.text
    assert routed.json()["config"]["channels"][0]["channel_id"] == "C123"

    # A later PATCH that omits config must preserve identity AND channels.
    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={
            "account_id": str(account.id),
            "credential_mode": "SYSTEM",
            "is_enabled": True,
        },
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["credential_mode"] == "SYSTEM"
    assert payload["config"]["identity"]["allowed_domains"] == ["lemma.test"]
    assert payload["config"]["channels"][0]["channel_id"] == "C123"


async def test_platform_webhook_verification_endpoints_and_signature_rejection(
    authenticated_client: AsyncClient,
    monkeypatch,
):

    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_verify_token", "verify-token")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    whatsapp = await authenticated_client.get(
        "/surfaces/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "challenge-123",
            "hub.verify_token": "verify-token",
        },
    )
    assert whatsapp.status_code == 200, whatsapp.text
    assert whatsapp.text == "challenge-123"

    telegram = await authenticated_client.get("/surfaces/webhooks/telegram")
    assert telegram.status_code == 200, telegram.text
    assert telegram.text == "ok"

    slack_verify = await authenticated_client.post(
        "/surfaces/webhooks/slack",
        json={"type": "url_verification", "challenge": "slack-challenge"},
    )
    assert slack_verify.status_code == 200, slack_verify.text
    assert slack_verify.json()["challenge"] == "slack-challenge"

    missing_signature = await authenticated_client.post(
        "/surfaces/webhooks/slack",
        json=_load_slack_dm_fixture(text="bad signature", ts="1700000000.333333"),
    )
    assert missing_signature.status_code == 401


async def test_surface_credentials_are_unique_within_org_until_deleted(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    primary_pod_id = test_pod["id"]
    sibling = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": test_pod["organization_id"],
            "name": "Surface credential sibling pod",
        },
    )
    assert sibling.status_code == 201, sibling.text
    sibling_pod_id = sibling.json()["id"]

    system_created = await authenticated_client.post(
        f"/pods/{primary_pod_id}/surfaces",
        json={"platform": "WHATSAPP"},
    )
    assert system_created.status_code == 200, system_created.text

    duplicate_system = await authenticated_client.post(
        f"/pods/{sibling_pod_id}/surfaces",
        json={"platform": "WHATSAPP"},
    )
    assert duplicate_system.status_code == 400, duplicate_system.text
    assert "System WHATSAPP credentials are already used" in duplicate_system.text

    deleted_system = await authenticated_client.delete(
        f"/pods/{primary_pod_id}/surfaces/whatsapp"
    )
    assert deleted_system.status_code == 204, deleted_system.text

    reused_system = await authenticated_client.post(
        f"/pods/{sibling_pod_id}/surfaces",
        json={"platform": "WHATSAPP"},
    )
    assert reused_system.status_code == 200, reused_system.text

    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-org-unique",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    account_created = await authenticated_client.post(
        f"/pods/{primary_pod_id}/surfaces",
        json={"platform": "SLACK", "account_id": str(account.id)},
    )
    assert account_created.status_code == 200, account_created.text

    duplicate_account = await authenticated_client.post(
        f"/pods/{sibling_pod_id}/surfaces",
        json={"platform": "SLACK", "account_id": str(account.id)},
    )
    assert duplicate_account.status_code == 400, duplicate_account.text
    assert "connected account is already used" in duplicate_account.text

    deleted_account = await authenticated_client.delete(
        f"/pods/{primary_pod_id}/surfaces/slack"
    )
    assert deleted_account.status_code == 204, deleted_account.text

    reused_account = await authenticated_client.post(
        f"/pods/{sibling_pod_id}/surfaces",
        json={"platform": "SLACK", "account_id": str(account.id)},
    )
    assert reused_account.status_code == 200, reused_account.text


async def test_surface_setup_actions_depend_on_auth_config_source(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    """A Slack account on Lemma's own app (SYSTEM_DEFAULT) needs no setup; only
    an account on the org's own Slack app (ORG_CUSTOM) produces action steps.
    The surface and its credential_mode are identical — only the account's auth
    config source differs."""
    from app.core.config import settings as app_settings
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "enable_slack_socket_mode", False)
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        config_source="SYSTEM_DEFAULT",
        credentials={
            "access_token": "xoxb-setup-actions",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )

    created = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={"platform": "SLACK", "account_id": str(account.id)},
    )
    assert created.status_code == 200, created.text

    # Lemma's own Slack app: webhook is wired up centrally → nothing to do.
    system_setup = (await authenticated_client.get(f"/pods/{pod_id}/surfaces/slack/setup")).json()
    assert system_setup["ready"] is True
    assert system_setup["actions"] == []

    # Flip the account's auth config to the org's own app — same surface.
    auth_config = await db_session.get(AuthConfig, account.auth_config_id)
    auth_config.config_source = "ORG_CUSTOM"
    await db_session.commit()

    custom_setup = (await authenticated_client.get(f"/pods/{pod_id}/surfaces/slack/setup")).json()
    assert custom_setup["ready"] is False
    assert len(custom_setup["actions"]) == 1
    action = custom_setup["actions"][0]
    assert action["key"] == "slack_event_subscriptions"
    assert action["steps"]
    assert action["link"] == "https://api.slack.com/apps"
    assert any(field["value"].endswith("/surfaces/webhooks/slack") for field in action["fields"])


async def test_surface_send_endpoint_and_send_policy_config(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-surface-send",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {"bot_user_id": "U0BOT", "team_id": "T0123456"},
        },
    )
    agent = await _create_agent(authenticated_client, pod_id)
    created = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "default_agent_name": agent["name"],
            "account_id": str(account.id),
            "config": {"send_policy": {"allow_send": True}},
        },
    )
    assert created.status_code == 200, created.text
    # send_policy round-trips through the config.
    assert created.json()["config"]["send_policy"]["allow_send"] is True

    # The member is a pod member but has no thread on this surface yet -> 404.
    resp = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces/slack/send",
        json={"user_id": fixed_test_user["id"], "message": "ping"},
    )
    assert resp.status_code == 404, resp.text


async def test_create_resend_email_surface_provisions_address(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    monkeypatch,
):
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.infrastructure.models import AgentSurface
    from sqlalchemy import select

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    agent = await _create_agent(authenticated_client, pod_id)

    # Resend is a system-credentialed email surface: no account_id needed, and
    # it must not require a Composio polling schedule.
    created = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={"platform": "RESEND", "default_agent_name": agent["name"]},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["platform"] == "RESEND"
    assert body["agent_name"] == agent["name"]

    # The per-pod inbound/outbound address is provisioned on creation.
    row = (
        await db_session.execute(
            select(AgentSurface).where(
                AgentSurface.pod_id == pod_id,
                AgentSurface.surface_type == "RESEND",
            )
        )
    ).scalar_one()
    assert row.surface_identity_email and row.surface_identity_email.endswith(
        "@ops.lemma.work"
    )
