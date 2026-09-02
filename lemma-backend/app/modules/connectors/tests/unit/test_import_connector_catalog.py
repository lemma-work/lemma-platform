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
    ConnectorKind,
    AuthMethod,
    AuthProvider,
    ComposioProviderCapability,
    DiscoveryMode,
    HttpKindSpec,
    LemmaProviderCapability,
    McpKindSpec,
    SqlKindSpec,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)

_MODULE_PATH = (
    Path(__file__).resolve().parents[5] / "scripts" / "import_connector_catalog.py"
)
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
async def test_sync_static_operations_stores_the_declared_kind():
    """Regression test: `_sync_static_operations` always upserted operations as
    `AuthProvider.LEMMA`, which `provider_to_kind` maps to `PACKAGE` as an
    ambiguous best-effort default -- so every static-operations connector's
    rows (not just github's: `sql`'s query/list_tables/describe_table too)
    were stored with `kind='package'` regardless of the connector's real
    declared kind. `get_by_connector_kind_and_name` -- what the execute-
    operation route uses -- looks up by the *real* kind, so every such
    operation was unreachable at runtime despite importing without error.
    """
    created: list = []

    class _FakeOperationRepository:
        async def get_by_connector_kind_and_name(self, connector_id, kind, name):
            del connector_id, kind, name
            return

        async def create(self, entity):
            created.append(entity)

        async def update(self, entity):
            created.append(entity)

    await importer._sync_static_operations(
        _FakeOperationRepository(),
        "github",
        [
            {
                "name": "users_get_authenticated",
                "description": "Get the authenticated user",
                "execution": {"kind": "http", "mode": "openapi"},
            }
        ],
        kind="http",
    )

    assert len(created) == 1
    assert created[0].kind == ConnectorKind.HTTP


@pytest.mark.asyncio
async def test_sync_native_catalog_honors_declared_kind():
    """A lemma_apps_config.json entry's "kind" field must drive the connector's
    capability class, not silently fall back to a vendored-package kind.

    Regression test: `_sync_native_catalog` previously never forwarded
    `app_config.get("kind")` into `_native_kind_spec`, so every connector
    sourced from lemma_apps_config.json -- including `sql`, `mcp`, `openapi`,
    and `github` -- was built as a `PackageKindSpec` regardless of its
    declared kind. That only surfaced once a real install tried to select
    the connector's own declared kind and got a "cannot be installed as"
    error, because no test exercised `_sync_native_catalog` end-to-end for a
    non-package kind.
    """
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "sql",
                    "title": "SQL Database",
                    "description": "Connect to an external SQL database.",
                    "auth_method": "API_KEY",
                    "kind": "sql",
                    "is_active": True,
                    "triggers": [],
                },
                {
                    "name": "github",
                    "title": "GitHub",
                    "description": "GitHub connector.",
                    "auth_method": "OAUTH2",
                    "kind": "http",
                    "is_active": True,
                    "triggers": [],
                },
            ],
        ),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"sql", "github"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    assert totals == (2, 0, 0)
    entities = {
        call.args[1].id: call.args[1] for call in upsert_connector.await_args_list
    }

    sql_spec = entities["sql"].spec_for(ConnectorKind.SQL)
    assert isinstance(sql_spec, SqlKindSpec)

    github_spec = entities["github"].spec_for(ConnectorKind.HTTP)
    assert isinstance(github_spec, HttpKindSpec)


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
        patch.object(
            importer, "_paginate_tools", return_value=iter([_tool("list_events")])
        ),
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
async def test_sync_composio_catalog_backfills_toolkit_logo_for_iconless_native_app():
    """Natively-supported apps ship with icon=None in lemma_apps_config.json; without
    a fallback they are the only connectors that never render a brand mark."""
    connector_repository = SimpleNamespace(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="slack",
                title="Slack",
                description="Slack connector",
                icon=None,
                provider_capabilities=[LemmaProviderCapability()],
                is_active=True,
            )
        )
    )
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit("slack", name="Slack")
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
        patch.object(importer, "_upsert_operation", AsyncMock()),
        patch.object(importer, "_upsert_trigger", AsyncMock()),
    ):
        await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"slack"},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    entity = upsert_connector.await_args.args[1]
    assert entity.icon == "slack.png"
    # The curated title/description are still preserved for native apps.
    assert entity.title == "Slack"
    assert entity.description == "Slack connector"


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
    # Always true: every Composio toolkit runs on Lemma's own Composio account.
    assert capability.system_default_available is True
    # None only because HubSpot is OAuth2 here. A non-OAuth toolkit carries the
    # end user's credential form in this field -- see the API_KEY case below.
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
async def test_sync_composio_catalog_imports_apollo_api_key_and_contact_operations():
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit("apollo", name="Apollo")
    toolkit_item.auth_schemes = ["API_KEY"]
    api_key_field = SimpleNamespace(
        name="generic_api_key",
        display_name="API Key",
        description="Apollo API key",
        type="string",
        default=None,
        is_secret=True,
        required=True,
    )
    toolkit_detail = SimpleNamespace(
        auth_config_details=[
            SimpleNamespace(
                mode="API_KEY",
                fields=SimpleNamespace(
                    connected_account_initiation=SimpleNamespace(
                        required=[api_key_field],
                        optional=[],
                    )
                ),
            )
        ]
    )
    operation_names = [
        "APOLLO_SEARCH_CONTACTS",
        "APOLLO_PEOPLE_SEARCH",
        "APOLLO_PEOPLE_ENRICHMENT",
        "APOLLO_LIST_EMAIL_ACCOUNTS",
    ]
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=toolkit_detail))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(
            importer,
            "_paginate_tools",
            return_value=iter([_tool(name) for name in operation_names]),
        ),
        patch.object(importer, "_paginate_triggers", return_value=iter([])),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()) as upsert_operation,
    ):
        totals = await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"apollo"},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    assert totals == (1, 4, 0)
    entity = upsert_connector.await_args.args[1]
    assert entity.id == "apollo"
    capability = _capability(entity, AuthProvider.COMPOSIO)
    assert capability.auth_scheme == AuthMethod.API_KEY
    assert capability.auth_config_schema == {
        "type": "object",
        "properties": {
            "generic_api_key": {
                "type": "string",
                "title": "API Key",
                "description": "Apollo API key",
                "format": "password",
            }
        },
        "additionalProperties": False,
        "required": ["generic_api_key"],
    }
    imported_operations = {
        call.kwargs["public_name"] for call in upsert_operation.await_args_list
    }
    assert imported_operations == set(operation_names)


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
    assert (
        _capability(entity, AuthProvider.COMPOSIO).toolkit_slug == "Exact_Composio_App"
    )

    upsert_operation.assert_awaited_once()
    assert upsert_operation.await_args.args[1] == "Exact_Composio_App"
    assert (
        upsert_operation.await_args.kwargs["public_name"] == "Exact_Composio_Operation"
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
        importer._resolve_composio_provider_operation_name(tool) == "outlook_send_email"
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
                provider_capabilities=[
                    ComposioProviderCapability(toolkit_slug=app_slug)
                ],
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
async def test_sync_composio_catalog_applies_curated_profile_operation_names():
    """A connector with a curated entry in the profile-operations override
    gets it threaded onto its ComposioProviderCapability during sync."""
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit("testapp", name="TestApp")
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=_toolkit_detail()))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(importer, "_paginate_tools", return_value=iter([])),
        patch.object(importer, "_paginate_triggers", return_value=iter([])),
        patch.object(
            importer,
            "_load_connector_profile_operations",
            return_value={"testapp": {"COMPOSIO": ["TESTAPP_GET_CURRENT_USER"]}},
        ),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()),
        patch.object(importer, "_upsert_trigger", AsyncMock()),
    ):
        await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"testapp"},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    entity = upsert_connector.await_args.args[1]
    capability = _capability(entity, AuthProvider.COMPOSIO)
    assert capability.profile_operation_names == ["TESTAPP_GET_CURRENT_USER"]


@pytest.mark.asyncio
async def test_sync_composio_catalog_leaves_profile_operation_names_none_without_override():
    """No regression for the ~50 apps with no curated entry yet: the field
    stays None, identical to today's behavior before this feature existed."""
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()

    toolkit_item = _toolkit("uncurated_app", name="Uncurated App")
    composio = SimpleNamespace(
        toolkits=SimpleNamespace(get=MagicMock(return_value=_toolkit_detail()))
    )

    with (
        patch.dict(os.environ, {"COMPOSIO_API_KEY": "test-api-key"}, clear=False),
        patch.object(importer, "Composio", return_value=composio),
        patch.object(importer, "_list_composio_toolkits", return_value=[toolkit_item]),
        patch.object(importer, "_paginate_tools", return_value=iter([])),
        patch.object(importer, "_paginate_triggers", return_value=iter([])),
        patch.object(importer, "_load_connector_profile_operations", return_value={}),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
        patch.object(importer, "_upsert_operation", AsyncMock()),
        patch.object(importer, "_upsert_trigger", AsyncMock()),
    ):
        await importer._sync_composio_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"uncurated_app"},
            managed_by="composio",
            page_size=100,
            max_composio_apps=10,
        )

    entity = upsert_connector.await_args.args[1]
    capability = _capability(entity, AuthProvider.COMPOSIO)
    assert capability.profile_operation_names is None


@pytest.mark.asyncio
async def test_sync_native_catalog_applies_curated_profile_operation_names():
    """JSON-config Lemma apps (Slack, Jira, Confluence, ...) also get curated
    profile operations threaded onto their LemmaProviderCapability."""
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = SimpleNamespace()
    trigger_repository = SimpleNamespace()
    schema_compiler = SimpleNamespace(
        to_json_schema=MagicMock(return_value={"type": "object"})
    )

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "testapp",
                    "title": "TestApp",
                    "description": "TestApp connector",
                    "auth_method": "OAUTH2",
                    "triggers": [],
                }
            ],
        ),
        patch.object(
            importer,
            "_load_connector_profile_operations",
            return_value={"testapp": {"LEMMA": ["get_profile"]}},
        ),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
    ):
        await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"testapp"},
            schema_compiler=schema_compiler,
        )

    entity = upsert_connector.await_args.args[1]
    capability = _capability(entity, AuthProvider.LEMMA)
    assert capability.profile_operation_names == ["get_profile"]


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
    assert _providers(upsert_connector.await_args_list[1].args[1]) == [
        AuthProvider.LEMMA
    ]
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
    assert "apollo" in fetched_slugs
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
            side_effect=lambda connector_id: (
                existing if connector_id == "microsoft_teams" else None
            )
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


def test_github_is_offered_natively_and_never_imported_from_composio():
    """A workspace agent's `git`/`gh` need the account's real token inside the
    sandbox, which a broker never hands over -- so GitHub is Lemma's own
    connector, and importing a Composio toolkit of the same name would put a
    second, unusable GitHub install next to it."""
    assert "github" not in importer.DEFAULT_COMPOSIO_CONNECTOR_IDS
    assert "github" in importer.COMPOSIO_EXCLUDED_CONNECTOR_IDS
    assert importer._filter_composio_connector_ids({"github", "notion"}) == {"notion"}


def test_a_retired_connector_is_never_deactivated_wholesale():
    """Excluding a connector from Composio normally means the whole connector
    goes: nothing is left once the toolkit is removed. A retired one still has
    its native half, so deactivating it here would take GitHub down entirely."""
    github = ConnectorEntity(
        id="github",
        title="GitHub",
        provider_capabilities=[
            HttpKindSpec(auth_scheme=AuthMethod.OAUTH2),
            ComposioProviderCapability(toolkit_slug="github"),
        ],
        is_active=True,
    )
    assert importer._is_excluded_composio_connector(github) is False


@pytest.mark.asyncio
async def test_retiring_composio_drops_only_its_half_of_the_connector():
    github = ConnectorEntity(
        id="github",
        title="GitHub",
        provider_capabilities=[
            HttpKindSpec(auth_scheme=AuthMethod.OAUTH2),
            ComposioProviderCapability(toolkit_slug="github"),
        ],
        is_active=True,
    )
    connector_repository = SimpleNamespace(
        get=AsyncMock(return_value=github),
        update=AsyncMock(),
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=2))
    )

    retired = await importer._retire_composio_capabilities(
        connector_repository, session
    )

    assert retired == 1
    updated = connector_repository.update.await_args.args[0]
    assert updated.is_active is True
    assert [
        AuthProvider(capability.provider.value)
        for capability in updated.provider_capabilities
    ] == [AuthProvider.LEMMA]
    # Catalog rows are regenerated every import, so the Composio ones are
    # deleted; installs are only disabled, because deleting them would silently
    # disconnect the people who own them.
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert any(
        "DELETE FROM connector_operations" in statement for statement in statements
    )
    assert any(
        "DELETE FROM connector_triggers" in statement for statement in statements
    )
    assert any("UPDATE auth_configs" in statement for statement in statements)
    assert not any("accounts" in statement.lower() for statement in statements)


@pytest.mark.asyncio
async def test_retiring_composio_is_a_no_op_once_applied():
    github = ConnectorEntity(
        id="github",
        title="GitHub",
        provider_capabilities=[HttpKindSpec(auth_scheme=AuthMethod.OAUTH2)],
        is_active=True,
    )
    connector_repository = SimpleNamespace(
        get=AsyncMock(return_value=github),
        update=AsyncMock(),
    )
    session = SimpleNamespace(execute=AsyncMock())

    assert (
        await importer._retire_composio_capabilities(connector_repository, session) == 0
    )
    session.execute.assert_not_awaited()
    connector_repository.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_composio_catalog_batched_commits_per_toolkit_batch():
    toolkit_items = [
        _toolkit("outlook", name="Outlook"),
        _toolkit("trello", name="Trello"),
    ]

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


def test_trigger_id_is_keyed_on_kind_like_the_uniqueness_index():
    """The index is unique on (connector, kind, event_type). Minting the id from
    the two-valued provider instead meant two rows the index considers distinct
    could collide on the primary key."""
    assert (
        importer._trigger_id("gmail", ConnectorKind.COMPOSIO.value, "New_Message")
        == "gmail:composio:new_message"
    )
    assert (
        importer._trigger_id("slack", ConnectorKind.PACKAGE.value, "msg")
        == "slack:package:msg"
    )
    # A native http connector no longer shares an id space with a package one.
    assert (
        importer._trigger_id("github", ConnectorKind.HTTP.value, "Push")
        == "github:http:push"
    )


def test_two_triggers_sharing_an_event_type_fail_the_import():
    with pytest.raises(ValueError, match="two triggers with event_type 'message'"):
        importer._reject_duplicate_trigger_events(
            "slack",
            [
                {"name": "slack_channel_message", "event_type": "message"},
                {"name": "slack_thread_reply", "event_type": "message"},
            ],
        )


def test_distinct_event_types_pass():
    importer._reject_duplicate_trigger_events(
        "slack",
        [
            {"name": "slack_channel_message", "event_type": "message"},
            {"name": "slack_thread_reply", "event_type": "message.thread"},
        ],
    )


@pytest.mark.asyncio
async def test_upsert_trigger_tags_kind():
    class _FakeTriggerRepo:
        def __init__(self):
            self.created = []

        async def get_by_connector_kind_and_name(self, app, kind, name):
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


# --- tenant-configured kinds (sql / mcp / http) -------------------------------


class _FakeOperationRepo:
    """Enough of the operation repository to observe kind tagging."""

    def __init__(self, existing: list[ConnectorOperationEntity] | None = None):
        self.rows = list(existing or [])
        self.created: list[ConnectorOperationEntity] = []
        self.updated: list[ConnectorOperationEntity] = []

    async def get_by_connector_kind_and_name(self, connector_id, kind, name):
        for row in self.rows:
            if (
                row.connector_id == connector_id
                and row.kind.value == kind
                and row.name == name
            ):
                return row
        return None

    async def create(self, entity):
        self.created.append(entity)
        return entity

    async def update(self, entity):
        self.updated.append(entity)
        return entity


def _sql_app_config() -> dict:
    return {
        "name": "sql",
        "title": "SQL Database",
        "description": "Connect to an external SQL database.",
        "auth_method": "API_KEY",
        "kind": "sql",
        "auth_config_schema": {
            "type": "object",
            "required": ["dialect", "host", "database"],
            "properties": {
                "dialect": {"type": "string"},
                "host": {"type": "string"},
                "database": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "credential_schema": {
            "type": "object",
            "required": ["username", "password"],
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string", "format": "password"},
            },
        },
        "is_active": True,
        "triggers": [],
        "static_operations": [
            {
                "name": "execute_query",
                "description": "Run a read-only SELECT.",
                "execution": {"kind": "sql", "op": "query"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        ],
    }


@pytest.mark.asyncio
async def test_sync_native_catalog_installs_sql_entry_as_the_sql_kind():
    """The catalog's own `kind` decides the executor; dropping it broke both."""
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = _FakeOperationRepo()
    trigger_repository = SimpleNamespace()

    with (
        patch.object(
            importer, "_load_lemma_apps_config", return_value=[_sql_app_config()]
        ),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"sql"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    assert totals == (1, 1, 0)
    entity = upsert_connector.await_args.args[1]
    capability = _capability(entity, AuthProvider.LEMMA)
    assert isinstance(capability, SqlKindSpec)
    assert capability.kind is ConnectorKind.SQL
    assert capability.auth_scheme == AuthMethod.API_KEY

    # The operation has to carry the same kind: execution resolves it by
    # (connector, install kind, name), so a package-tagged row is invisible to
    # a sql install.
    assert len(operation_repository.created) == 1
    operation = operation_repository.created[0]
    assert operation.kind is ConnectorKind.SQL
    assert operation.id == "sql:sql:execute_query"


@pytest.mark.asyncio
async def test_sync_native_catalog_marks_mcp_entry_for_tool_discovery():
    connector_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    operation_repository = _FakeOperationRepo()
    trigger_repository = SimpleNamespace()

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "mcp",
                    "title": "MCP Server",
                    "auth_method": "API_KEY",
                    "kind": "mcp",
                    "description": "Connect an MCP server.",
                    "auth_config_schema": {
                        "type": "object",
                        "required": ["server_url"],
                        "properties": {"server_url": {"type": "string"}},
                    },
                    "is_active": True,
                    "triggers": [],
                }
            ],
        ),
        patch.object(importer, "_upsert_connector", AsyncMock()) as upsert_connector,
    ):
        await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"mcp"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    capability = _capability(upsert_connector.await_args.args[1], AuthProvider.LEMMA)
    assert isinstance(capability, McpKindSpec)
    assert capability.discovery is DiscoveryMode.MCP


@pytest.mark.asyncio
async def test_upsert_operation_retags_a_legacy_package_row_in_place():
    """Re-running the import must migrate, not duplicate.

    The unique index is (connector, kind, name), so a fresh insert under the
    new kind would leave the package-tagged row behind and the catalog would
    list the operation twice.
    """
    legacy = ConnectorOperationEntity(
        id="sql:package:execute_query",
        connector_id="sql",
        kind=ConnectorKind.PACKAGE,
        name="execute_query",
        provider_operation_name="execute_query",
    )
    repo = _FakeOperationRepo([legacy])

    await importer._upsert_operation(
        repo,
        "sql",
        provider=AuthProvider.LEMMA,
        public_name="execute_query",
        provider_operation_name="execute_query",
        display_name="Execute query",
        description="Run a read-only SELECT.",
        input_schema=None,
        output_schema=None,
        search_document=None,
        execution={"kind": "sql", "op": "query"},
        kind="sql",
    )

    assert repo.created == []
    assert len(repo.updated) == 1
    assert repo.updated[0].id == "sql:package:execute_query"
    assert repo.updated[0].kind is ConnectorKind.SQL


# --- connector id rename migration -------------------------------------------


class _FakeRenameResult:
    rowcount = 1


class _FakeRenameSession:
    """Records executed statements so tests can assert the re-point-then-delete
    order without a real database."""

    def __init__(self) -> None:
        self.executed: list = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        return _FakeRenameResult()


class _FakeConnectorRepoForRename:
    def __init__(self, existing_ids: set[str]) -> None:
        self._existing = existing_ids

    async def get(self, connector_id: str):
        return object() if connector_id in self._existing else None


def _rename_ops(session: _FakeRenameSession) -> list[str]:
    return [" ".join(sql.split()[:2]) for sql, _ in session.executed]


async def test_apply_connector_renames_repoints_then_deletes():
    session = _FakeRenameSession()
    repo = _FakeConnectorRepoForRename({"teams", "microsoft_teams"})
    with patch.object(importer, "CONNECTOR_ID_RENAMES", {"teams": "microsoft_teams"}):
        renamed = await importer._apply_connector_renames(repo, session)
    assert renamed == 1
    # Accounts + auth_configs are re-pointed BEFORE the old connector is deleted;
    # deleting first would cascade-delete every connected account.
    assert _rename_ops(session) == [
        "UPDATE accounts",
        "UPDATE auth_configs",
        "DELETE FROM",
    ]
    for _, params in session.executed:
        assert params["old"] == "teams"
        assert params.get("new", "microsoft_teams") == "microsoft_teams"


async def test_apply_connector_renames_skips_when_old_absent():
    session = _FakeRenameSession()
    repo = _FakeConnectorRepoForRename({"microsoft_teams"})  # already migrated
    with patch.object(importer, "CONNECTOR_ID_RENAMES", {"teams": "microsoft_teams"}):
        renamed = await importer._apply_connector_renames(repo, session)
    assert renamed == 0
    assert session.executed == []


async def test_apply_connector_renames_skips_when_target_not_synced():
    # Safety: if the new connector isn't in the catalog yet, do NOT touch/delete
    # the old one — re-pointing to a missing FK target or deleting the old row
    # (ON DELETE CASCADE) would break or destroy live accounts.
    session = _FakeRenameSession()
    repo = _FakeConnectorRepoForRename({"teams"})
    with patch.object(importer, "CONNECTOR_ID_RENAMES", {"teams": "microsoft_teams"}):
        renamed = await importer._apply_connector_renames(repo, session)
    assert renamed == 0
    assert session.executed == []


def test_a_second_native_kind_survives_the_merge():
    """Keying the merge on the two-valued auth provider collapsed every native
    kind onto one slot, so a connector gaining an `http` spec silently lost its
    `package` one -- which is exactly what a package-to-http migration does."""
    slack = ConnectorEntity(
        id="slack",
        title="Slack",
        provider_capabilities=[
            LemmaProviderCapability(auth_scheme=AuthMethod.OAUTH2),
            ComposioProviderCapability(toolkit_slug="slack"),
        ],
    )

    merged = importer._merge_provider_capabilities(
        slack, HttpKindSpec(auth_scheme=AuthMethod.OAUTH2)
    )

    assert [capability.kind for capability in merged] == [
        ConnectorKind.HTTP,
        ConnectorKind.PACKAGE,
        ConnectorKind.COMPOSIO,
    ]


def test_merging_the_same_kind_twice_replaces_rather_than_duplicates():
    github = ConnectorEntity(
        id="github",
        title="GitHub",
        provider_capabilities=[HttpKindSpec(auth_scheme=AuthMethod.OAUTH2)],
    )

    merged = importer._merge_provider_capabilities(
        github, HttpKindSpec(auth_scheme=AuthMethod.API_KEY)
    )

    assert [capability.kind for capability in merged] == [ConnectorKind.HTTP]
    assert merged[0].auth_scheme is AuthMethod.API_KEY


@pytest.mark.asyncio
async def test_a_native_http_connector_seeds_triggers_under_its_own_kind():
    """Triggers were written under `package` regardless of the entry's kind,
    while `list_triggers_for_auth_config` reads them back by the *install's*
    kind. For an http connector like GitHub the rows existed and the API could
    never return them."""
    connector_repository = _ConnectorRepository()
    operation_repository = AsyncMock()
    trigger_repository = AsyncMock()
    trigger_repository.get_by_connector_kind_and_name = AsyncMock(return_value=None)

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "github",
                    "title": "GitHub",
                    "description": "GitHub connector",
                    "auth_method": "OAUTH2",
                    "kind": "http",
                    "triggers": [
                        {
                            "name": "github_pull_request_opened",
                            "event_type": "pull_request.opened",
                            "description": "A pull request was opened",
                        }
                    ],
                }
            ],
        ),
        patch.object(importer, "_list_native_apps", return_value=[]),
    ):
        totals = await importer._sync_native_catalog(
            connector_repository,
            operation_repository,
            trigger_repository,
            app_filters={"github"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    assert totals[2] == 1
    created = trigger_repository.create.await_args.args[0]
    assert created.kind is ConnectorKind.HTTP
    assert created.id == "github:http:pull_request.opened"
    # The lookup has to use the same kind, or every import creates a duplicate.
    lookup = trigger_repository.get_by_connector_kind_and_name.await_args.args
    assert lookup[1] == ConnectorKind.HTTP.value


@pytest.mark.asyncio
async def test_slacks_seeded_install_schema_asks_for_the_signing_secret():
    """An organization running its own Slack app has to supply a signing secret
    or its webhooks cannot be verified at all. The seeder had its own copy of
    the default-schema rule that took no connector id, so it could never
    produce that field -- and because it wrote *a* schema into the catalog, the
    read-time default that does know about Slack never got a chance to."""
    connector_repository = _ConnectorRepository()

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
                    "oauth2_config": {
                        "authorization_url": "https://slack.com/oauth/v2/authorize",
                        "token_url": "https://slack.com/api/oauth.v2.access",
                    },
                    "triggers": [],
                }
            ],
        ),
        patch.object(importer, "_list_native_apps", return_value=[]),
    ):
        await importer._sync_native_catalog(
            connector_repository,
            AsyncMock(),
            AsyncMock(),
            app_filters={"slack"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    schema = _capability(
        connector_repository.entity, AuthProvider.LEMMA
    ).auth_config_schema
    assert "signing_secret" in schema["properties"]
    assert "signing_secret" in schema["required"]


@pytest.mark.asyncio
async def test_a_connector_without_its_own_quirks_gets_the_plain_oauth_schema():
    connector_repository = _ConnectorRepository()

    with (
        patch.object(
            importer,
            "_load_lemma_apps_config",
            return_value=[
                {
                    "name": "github",
                    "title": "GitHub",
                    "description": "GitHub connector",
                    "auth_method": "OAUTH2",
                    "kind": "http",
                    "oauth2_config": {
                        "authorization_url": "https://github.com/login/oauth/authorize",
                        "token_url": "https://github.com/login/oauth/access_token",
                    },
                    "triggers": [],
                }
            ],
        ),
        patch.object(importer, "_list_native_apps", return_value=[]),
    ):
        await importer._sync_native_catalog(
            connector_repository,
            AsyncMock(),
            AsyncMock(),
            app_filters={"github"},
            schema_compiler=importer.PydanticCodeSchemaCompiler(),
        )

    schema = _capability(
        connector_repository.entity, AuthProvider.LEMMA
    ).auth_config_schema
    assert sorted(schema["required"]) == ["client_id", "client_secret"]


def _toolkit_with_modes(*modes: str):
    return SimpleNamespace(
        no_auth=False,
        auth_schemes=[],
        composio_managed_auth_schemes=[],
    ), SimpleNamespace(
        auth_config_details=[SimpleNamespace(mode=mode) for mode in modes]
    )


def test_a_dynamically_registered_oauth_client_is_still_oauth():
    """Composio registers the client for `DCR_OAUTH` rather than it being
    configured ahead of time, but the person still consents through a redirect.
    Falling through to API_KEY offered them a form asking for a key that does
    not exist -- which is what Granola would have shipped as."""
    item, detail = _toolkit_with_modes("DCR_OAUTH")
    assert importer._infer_composio_auth_method(item, detail) is AuthMethod.OAUTH2


def test_an_api_key_toolkit_is_unchanged():
    item, detail = _toolkit_with_modes("API_KEY")
    assert importer._infer_composio_auth_method(item, detail) is AuthMethod.API_KEY


def test_no_auth_still_wins_over_everything():
    item, detail = _toolkit_with_modes("NO_AUTH", "DCR_OAUTH")
    assert importer._infer_composio_auth_method(item, detail) is AuthMethod.NOAUTH


def test_the_meeting_and_warehouse_apps_are_in_the_default_catalog():
    """By Composio's slugs, which are not the names people use for them."""
    ids = set(importer.DEFAULT_COMPOSIO_CONNECTOR_IDS)
    assert {"granola_mcp", "fireflies", "googlebigquery"} <= ids
