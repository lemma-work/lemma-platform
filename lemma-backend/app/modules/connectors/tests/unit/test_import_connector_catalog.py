from __future__ import annotations

import os
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    AuthMethod,
    AuthProvider,
    ComposioProviderCapability,
    LemmaProviderCapability,
)

_MODULE_PATH = Path(__file__).resolve().parents[5] / "scripts" / "import_connector_catalog.py"
_SPEC = importlib.util.spec_from_file_location("import_connector_catalog", _MODULE_PATH)
assert _SPEC and _SPEC.loader
importer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(importer)


def _toolkit(slug: str, *, name: str = "App") -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        name=name,
        meta=SimpleNamespace(description=f"{name} description", logo=f"{slug}.png"),
        status="ACTIVE",
        no_auth=False,
        auth_schemes=["OAUTH2"],
        composio_managed_auth_schemes=[],
    )


def _toolkit_detail() -> SimpleNamespace:
    return SimpleNamespace(auth_config_details=[])


def _tool(slug: str) -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        name=slug.replace("_", " ").title(),
        description=f"{slug} description",
        input_parameters={"type": "object"},
        output_parameters={"type": "object"},
    )


def _trigger(slug: str) -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        description=f"{slug} description",
        config={"type": "object"},
        payload={"type": "object"},
    )


def _operation_details(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        description=f"{name} description",
        implementation_content=None,
        input_schema_content="class InputSchema: pass",
        output_schema_content="class OutputSchema: pass",
    )


def _providers(entity: ConnectorEntity) -> list[AuthProvider]:
    return [capability.provider for capability in entity.provider_capabilities]


def _capability(entity: ConnectorEntity, provider: AuthProvider):
    return entity.capability_for(provider)


class _ConnectorRepository:
    def __init__(self, existing: ConnectorEntity | None = None):
        self.entity = existing

    async def get(self, connector_id: str) -> ConnectorEntity | None:
        if self.entity and self.entity.id == connector_id:
            return self.entity
        return None

    async def create(self, entity: ConnectorEntity) -> ConnectorEntity:
        self.entity = entity
        return entity

    async def update(self, entity: ConnectorEntity) -> ConnectorEntity:
        self.entity = entity
        return entity


@pytest.mark.asyncio
async def test_sync_native_catalog_imports_credential_only_surface_apps():
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()
    credential_schema = {
        "type": "object",
        "required": ["bot_token"],
        "properties": {"bot_token": {"type": "string", "format": "password"}},
    }

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "telegram",
                    "title": "Telegram",
                    "description": "Telegram bot surface connector",
                    "auth_method": "API_KEY",
                    "credential_schema": credential_schema,
                    "is_active": True,
                    "triggers": [],
                }
            ],
        ),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"telegram"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    assert totals == (1, 0, 0)
    entity = upsert_connector.await_args.args[1]
    assert entity.id == "telegram"
    capability = _capability(entity, AuthProvider.LEMMA)
    assert isinstance(capability, LemmaProviderCapability)
    assert capability.auth_scheme == AuthMethod.API_KEY
    assert capability.credential_schema == credential_schema
    assert capability.auth_config_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


@pytest.mark.asyncio
async def test_sync_native_catalog_adds_default_oauth_auth_config_schema():
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "custom-oauth",
                    "title": "Custom OAuth",
                    "description": "Custom OAuth connector",
                    "auth_method": "OAUTH2",
                    "oauth2_config": {
                        "authorization_url": "https://example.test/auth",
                        "token_url": "https://example.test/token",
                    },
                    "is_active": True,
                    "triggers": [],
                }
            ],
        ),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"custom-oauth"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    assert totals == (1, 0, 0)
    entity = upsert_connector.await_args.args[1]
    capability = _capability(entity, AuthProvider.LEMMA)
    assert capability.supports_org_custom_oauth is True
    assert capability.auth_config_schema == {
        "type": "object",
        "required": ["client_id", "client_secret"],
        "properties": {
            "client_id": {"type": "string", "title": "Client ID"},
            "client_secret": {
                "type": "string",
                "title": "Client secret",
                "format": "password",
            },
        },
        "additionalProperties": False,
    }


@pytest.mark.asyncio
async def test_sync_native_catalog_replaces_stale_slack_oauth_defaults():
    existing = ConnectorEntity(
        id="slack",
        title="Slack",
        description="Slack connector",
        provider_capabilities=[LemmaProviderCapability()],
    )
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=existing))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "slack",
                    "title": "Slack",
                    "description": "Slack connector",
                    "auth_method": "OAUTH2",
                    "system_oauth": {
                        "client_id_env": "SLACK_CLIENT_ID",
                        "client_secret_env": "SLACK_CLIENT_SECRET",
                    },
                    "oauth2_config": {
                        "authorization_url": "https://slack.com/oauth/v2/authorize",
                        "token_url": "https://slack.com/api/oauth.v2.access",
                        "default_scopes": ["chat:write"],
                        "extra_params": {"user_scope": "users:read"},
                    },
                    "triggers": [],
                }
            ],
        ),
        patch.object(importer, "_list_native_apps", return_value=[]),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"slack"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    assert totals == (1, 0, 0)
    entity = upsert_connector.await_args.args[1]
    capability = _capability(entity, AuthProvider.LEMMA)
    assert capability.oauth2_defaults is not None
    assert capability.oauth2_defaults.authorization_url == (
        "https://slack.com/oauth/v2/authorize"
    )
    assert capability.oauth2_defaults.token_url == (
        "https://slack.com/api/oauth.v2.access"
    )
    assert capability.supports_org_custom_oauth is True
    assert capability.system_oauth is not None


@pytest.mark.asyncio
async def test_sync_native_catalog_package_pass_preserves_slack_oauth_defaults():
    connector_repository = _ConnectorRepository()
    operation_repository = AsyncMock()
    trigger_repository = AsyncMock()
    info_client = SimpleNamespace(list_operations=AsyncMock(return_value=[]))

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "slack",
                    "title": "Slack",
                    "description": "Slack connector",
                    "auth_method": "OAUTH2",
                    "system_oauth": {
                        "client_id_env": "SLACK_CLIENT_ID",
                        "client_secret_env": "SLACK_CLIENT_SECRET",
                    },
                    "oauth2_config": {
                        "authorization_url": "https://slack.com/oauth/v2/authorize",
                        "token_url": "https://slack.com/api/oauth.v2.access",
                        "default_scopes": ["chat:write"],
                        "extra_params": {"user_scope": "users:read"},
                    },
                    "triggers": [],
                }
            ],
        ),
        patch.object(importer, "_list_native_apps", return_value=["slack"]),
        patch.object(importer, "get_native_info_client", return_value=info_client),
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"slack"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    assert totals == (2, 0, 0)
    assert connector_repository.entity is not None
    capability = _capability(connector_repository.entity, AuthProvider.LEMMA)
    assert capability.oauth2_defaults is not None
    assert capability.oauth2_defaults.authorization_url == (
        "https://slack.com/oauth/v2/authorize"
    )
    assert capability.oauth2_defaults.token_url == (
        "https://slack.com/api/oauth.v2.access"
    )
    assert capability.system_oauth is not None


@pytest.mark.asyncio
async def test_sync_composio_catalog_uses_googlecalendar_toolkit_with_google_calendar_app_id():
    connector_repository = SimpleNamespace(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="google_calendar",
                title="Google Calendar",
                description="Google Calendar connector",
                icon="googlecalendar.png",
                provider_capabilities=[LemmaProviderCapability()],
                is_active=True,
            )
        )
    )
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit("googlecalendar", name="Google Calendar")
    trigger = _trigger("event_created")
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=_toolkit_detail()))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(importer, "_paginate_tools", return_value=iter([_tool("list_events")])),
        patch.object(importer, "_paginate_triggers", return_value=iter([trigger])),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
        patch.object(importer, "_upsert_trigger", AsyncMock()) as upsert_trigger,
    ):
        totals = await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"googlecalendar"},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    assert totals == (1, 1, 1)
    connector_repository.get.assert_any_await("google_calendar")

    entity = upsert_connector.await_args.args[1]
    assert entity.id == "google_calendar"
    assert _providers(entity) == [AuthProvider.LEMMA, AuthProvider.COMPOSIO]
    assert entity.icon == "googlecalendar.png"
    assert _capability(entity, AuthProvider.COMPOSIO).toolkit_slug == "googlecalendar"

    upsert_operation.assert_awaited_once()
    upsert_trigger.assert_awaited_once()
    assert upsert_trigger.await_args.args[1] == "google_calendar"


@pytest.mark.asyncio
async def test_sync_composio_catalog_keeps_composio_operations_for_non_native_apps():
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit("hubspot", name="HubSpot")
    tool = _tool("hubspot_list_contacts")
    trigger = _trigger("hubspot_contact_created")
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=_toolkit_detail()))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(importer, "_paginate_tools", return_value=iter([tool])),
        patch.object(importer, "_paginate_triggers", return_value=iter([trigger])),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
        patch.object(importer, "_upsert_trigger", AsyncMock()) as upsert_trigger,
    ):
        totals = await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"hubspot"},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    assert totals == (1, 1, 1)
    connector_repository.get.assert_any_await("hubspot")

    entity = upsert_connector.await_args.args[1]
    assert entity.id == "hubspot"
    assert _providers(entity) == [AuthProvider.COMPOSIO]
    capability = _capability(entity, AuthProvider.COMPOSIO)
    assert capability.toolkit_slug == "hubspot"
    assert capability.system_default_available is True
    assert capability.supports_org_custom_auth_config is False
    assert capability.auth_config_schema is None

    upsert_operation.assert_awaited_once()
    assert upsert_operation.await_args.args[1] == "hubspot"
    assert upsert_operation.await_args.kwargs["public_name"] == "hubspot_list_contacts"
    assert (
        upsert_operation.await_args.kwargs["provider_operation_name"]
        == "hubspot_list_contacts"
    )
    upsert_trigger.assert_awaited_once()
    assert upsert_trigger.await_args.args[1] == "hubspot"


@pytest.mark.asyncio
async def test_sync_composio_catalog_preserves_exact_composio_app_and_operation_names():
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit("Exact_Composio_App", name="Exact Composio App")
    tool = _tool("Exact_Composio_Operation")
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=_toolkit_detail()))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(importer, "_paginate_tools", return_value=iter([tool])),
        patch.object(importer, "_paginate_triggers", return_value=iter([])),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
    ):
        totals = await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"Exact_Composio_App"},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    assert totals == (1, 1, 0)
    connector_repository.get.assert_any_await("Exact_Composio_App")

    entity = upsert_connector.await_args.args[1]
    assert entity.id == "Exact_Composio_App"
    assert _capability(entity, AuthProvider.COMPOSIO).toolkit_slug == "Exact_Composio_App"

    upsert_operation.assert_awaited_once()
    assert upsert_operation.await_args.args[1] == "Exact_Composio_App"
    assert (
        upsert_operation.await_args.kwargs["public_name"]
        == "Exact_Composio_Operation"
    )
    assert (
        upsert_operation.await_args.kwargs["provider_operation_name"]
        == "Exact_Composio_Operation"
    )
    assert upsert_operation.await_args.kwargs["normalize_name"] is False


def test_composio_provider_operation_name_is_exact_tool_slug():
    tool = SimpleNamespace(
        slug="outlook_send_email",
        enum="OUTLOOK_SEND_EMAIL",
        tool_name="OUTLOOK_SEND_EMAIL",
        provider_operation_name="OUTLOOK_SEND_EMAIL",
    )

    assert (
        importer._resolve_composio_provider_operation_name(tool)
        == "outlook_send_email"
    )


def test_paginate_tools_uses_sdk_toolkit_versions():
    tools_list = MagicMock(
        return_value=SimpleNamespace(
            items=[_tool("outlook_send_email")],
            next_cursor=None,
        )
    )
    composio = SimpleNamespace(
        client=SimpleNamespace(tools=SimpleNamespace(list=tools_list)),
        tools=SimpleNamespace(_toolkit_versions={"outlook": "20260511_01"}),
    )

    items = list(
        importer._paginate_tools(composio, toolkit_slug="outlook", page_size=100)
    )

    assert [item.slug for item in items] == ["outlook_send_email"]
    tools_list.assert_called_once_with(
        toolkit_slug="outlook",
        limit=100,
        cursor=None,
        toolkit_versions={"outlook": "20260511_01"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "app_slug",
    [
        "slack",
        "jira",
        "confluence",
    ],
)
async def test_sync_composio_catalog_uses_lemma_auth_provider_for_native_auth_apps(
    app_slug: str,
):
    connector_repository = SimpleNamespace(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id=app_slug,
                title=app_slug.title(),
                description=f"{app_slug.title()} connector",
                provider_capabilities=[ComposioProviderCapability(toolkit_slug=app_slug)],
                is_active=True,
            )
        )
    )
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit(app_slug, name=app_slug.title())
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=_toolkit_detail()))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(importer, "_paginate_tools", return_value=iter([])),
        patch.object(importer, "_paginate_triggers", return_value=iter([])),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
        patch.object(importer, "_upsert_trigger", AsyncMock()) as upsert_trigger,
    ):
        totals = await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={app_slug},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    assert totals == (1, 0, 0)
    connector_repository.get.assert_any_await(app_slug)

    entity = upsert_connector.await_args.args[1]
    assert entity.id == app_slug
    assert AuthProvider.COMPOSIO in _providers(entity)

    upsert_operation.assert_not_awaited()
    upsert_trigger.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("toolkit_slug", "expected_app_id"),
    [
        ("gmail", "gmail"),
        ("googlecalendar", "google_calendar"),
        ("googledrive", "google_drive"),
        ("googledocs", "google_docs"),
        ("googlesheets", "google_sheets"),
    ],
)
async def test_sync_composio_catalog_supports_both_providers_for_google_apps(
    toolkit_slug: str,
    expected_app_id: str,
):
    connector_repository = SimpleNamespace(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id=expected_app_id,
                title=expected_app_id.title(),
                description=f"{expected_app_id.title()} connector",
                provider_capabilities=[LemmaProviderCapability()],
                is_active=True,
            )
        )
    )
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit(toolkit_slug, name=toolkit_slug.title())
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=_toolkit_detail()))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(importer, "_paginate_tools", return_value=iter([])),
        patch.object(importer, "_paginate_triggers", return_value=iter([])),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
        patch.object(importer, "_upsert_trigger", AsyncMock()) as upsert_trigger,
    ):
        totals = await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={toolkit_slug},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    assert totals == (1, 0, 0)
    entity = upsert_connector.await_args.args[1]
    assert entity.id == expected_app_id
    assert _providers(entity) == [AuthProvider.LEMMA, AuthProvider.COMPOSIO]
    assert _capability(entity, AuthProvider.COMPOSIO).toolkit_slug == toolkit_slug
    upsert_operation.assert_not_awaited()
    upsert_trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_native_catalog_imports_slack_operations_from_lemma_packages():
    connector_repository = SimpleNamespace(
        get=AsyncMock(side_effect=[None, None]),
    )
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()
    info_client = SimpleNamespace(
        get_connector_info=AsyncMock(
            return_value=SimpleNamespace(
                platform_name="Slack",
                description="Slack connector",
                agent_guide="Use Slack",
            )
        ),
        list_available_operations=AsyncMock(
            return_value=["send_message", "get_channel_info"]
        ),
        get_operation_details=AsyncMock(
            side_effect=lambda name: _operation_details(name)
        ),
    )
    schema_compiler = SimpleNamespace(
        to_json_schema=MagicMock(return_value={"type": "object"})
    )

    with (
            patch.object(
                importer,
                "_load_lemma_apps_config",
                return_value=[
                    {
                        "name": "slack",
                        "title": "Slack",
                        "description": "Slack connector",
                        "auth_method": "OAUTH2",
                        "auth_provider": "LEMMA",
                        "operation_executor": "LEMMA",
                        "config": {
                            "access_token_path": "authed_user.access_token",
                            "refresh_token_path": "refresh_token",
                        },
                        "triggers": [],
                    }
                ],
            ),
            patch.object(
                importer, "get_native_info_client", AsyncMock(return_value=info_client)
            ) as get_native_info_client,
            patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
            patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
        ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"slack"},
            schema_compiler=schema_compiler,
        )

    assert totals == (2, 2, 0)
    assert connector_repository.get.await_args_list[0].args == ("slack",)
    assert connector_repository.get.await_args_list[1].args == ("slack",)
    assert upsert_connector.await_args_list[1].args[1].id == "slack"
    assert _providers(upsert_connector.await_args_list[1].args[1]) == [AuthProvider.LEMMA]
    get_info_client_call = get_native_info_client.await_args
    assert get_info_client_call.args == ("slack",)
    assert upsert_operation.await_count == 2
    assert upsert_operation.await_args_list[0].args[1] == "slack"
    assert upsert_operation.await_args_list[0].kwargs["public_name"] == "send_message"
    assert (
        upsert_operation.await_args_list[1].kwargs["public_name"] == "get_channel_info"
    )


@pytest.mark.asyncio
async def test_sync_native_catalog_imports_google_apps_for_lemma_provider():
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    with (
        patch.object(importer, "_load_lemma_apps_config", return_value=[]),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"gmail"},
            schema_compiler=SimpleNamespace(
                to_json_schema=MagicMock(return_value={"type": "object"})
            ),
        )

    assert totals[0] == 1
    assert totals[1] > 0
    entity = upsert_connector.await_args.args[1]
    assert entity.id == "gmail"
    assert _providers(entity) == [AuthProvider.LEMMA]
    assert upsert_operation.await_count == totals[1]


def test_list_composio_toolkits_uses_curated_allowlist_and_env_append():
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(
            get=MagicMock(side_effect=lambda slug: _toolkit(slug, name=slug.title()))
        )
    )

    with patch.dict(
        os.environ,
        {
            importer.COMPOSIO_EXTRA_CONNECTOR_IDS_ENV: (
                "custom_app, composio, microsoft_teams"
            )
        },
        clear=False,
    ):
        items = importer._list_composio_toolkits(
            composio,
            app_filters=None,
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    fetched_slugs = [item.slug for item in items]
    assert "outlook" in fetched_slugs
    assert "microsoft_teams" not in fetched_slugs
    assert "trello" in fetched_slugs
    assert "instagram" in fetched_slugs
    assert "metaads" in fetched_slugs
    assert "zoho_mail" in fetched_slugs
    assert "asana" in fetched_slugs
    assert "custom_app" in fetched_slugs
    assert "composio" not in fetched_slugs
    composio.toolkits.get.assert_any_call("custom_app")


def test_list_composio_toolkits_excludes_microsoft_teams_filter():
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(
            get=MagicMock(side_effect=lambda slug: _toolkit(slug, name=slug.title()))
        )
    )

    items = importer._list_composio_toolkits(
        composio,
        app_filters={"Microsoft_Teams"},
        managed_by="composio",
        page_size=100,
        max_composio_apps=10,
    )

    assert items == []
    composio.toolkits.get.assert_not_called()


def test_list_composio_toolkits_preserves_exact_filter_names():
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(
            get=MagicMock(side_effect=lambda slug: _toolkit(slug, name=slug.title()))
        )
    )

    items = importer._list_composio_toolkits(
        composio,
        app_filters={"Exact_Composio_App"},
        managed_by="composio",
        page_size=100,
        max_composio_apps=10,
    )

    assert [item.slug for item in items] == ["Exact_Composio_App"]
    composio.toolkits.get.assert_called_once_with("Exact_Composio_App")


@pytest.mark.asyncio
async def test_deactivate_excluded_composio_connectors_deactivates_microsoft_teams():
    existing = ConnectorEntity(
        id="microsoft_teams",
        title="Microsoft Teams",
        provider_capabilities=[
            ComposioProviderCapability(toolkit_slug="microsoft_teams")
        ],
        is_active=True,
    )
    connector_repository = SimpleNamespace(
        # Only microsoft_teams exists in the DB; other excluded ids resolve to None.
        get=AsyncMock(
            side_effect=lambda connector_id: existing
            if connector_id == "microsoft_teams"
            else None
        ),
        update=AsyncMock(),
    )

    count = await importer._deactivate_excluded_composio_connectors(
        connector_repository
    )

    assert count == 1
    connector_repository.get.assert_any_await("microsoft_teams")
    connector_repository.update.assert_awaited_once()
    updated = connector_repository.update.await_args.args[0]
    assert updated.id == "microsoft_teams"
    assert updated.is_active is False


@pytest.mark.asyncio
async def test_sync_composio_catalog_batched_commits_per_toolkit_batch():
    toolkit_items = [_toolkit("outlook", name="Outlook"), _toolkit("trello", name="Trello")]

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=SimpleNamespace()),
        patch.object(importer, "_list_composio_toolkits", return_value=toolkit_items),
        patch.object(
            importer,
            "_deactivate_excluded_composio_connectors_batch",
            AsyncMock(return_value=(0, 0, 0)),
        ),
        patch.object(
            importer,
            "_run_in_session_batch",
            AsyncMock(side_effect=[(0, 0, 0), (1, 2, 3), (1, 4, 5)]),
        ) as run_batch,
    ):
        totals = await importer._sync_composio_catalog_batched(
            app_filters=None,
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
            dry_run=False,
        )

    assert totals == (2, 6, 8)
    assert run_batch.await_count == 3


def test_trigger_id_includes_provider():
    assert (
        importer._trigger_id("gmail", AuthProvider.COMPOSIO, "New_Message")
        == "gmail:composio:new_message"
    )
    assert (
        importer._trigger_id("slack", AuthProvider.LEMMA, "msg") == "slack:lemma:msg"
    )


@pytest.mark.asyncio
async def test_upsert_trigger_tags_provider():
    class _FakeTriggerRepo:
        def __init__(self):
            self.created = []

        async def get_by_connector_provider_and_name(self, app, provider, name):
            return None

        async def create(self, entity):
            self.created.append(entity)
            return entity

        async def update(self, entity):  # pragma: no cover - not hit on create path
            return entity

    repo = _FakeTriggerRepo()
    await importer._upsert_trigger(
        repo, "gmail", _trigger("new_message"), provider=AuthProvider.COMPOSIO
    )

    assert len(repo.created) == 1
    entity = repo.created[0]
    assert entity.provider == AuthProvider.COMPOSIO
    assert entity.id == "gmail:composio:new_message"
    assert entity.event_type == "new_message"
