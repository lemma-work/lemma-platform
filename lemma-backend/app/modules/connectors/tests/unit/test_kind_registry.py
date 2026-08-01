"""The kind registry and dispatcher: one lookup, one timeout boundary.

Dispatch used to happen twice -- provider chose a gateway, then the gateway read
the operation descriptor to choose an executor -- and the timeout lived in the
middle of it, covering execution but not discovery. These pin the replacement:
every kind resolves through one registry, and nothing runs unbounded.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.connector import (
    ConnectorKind,
    McpKindSpec,
    PackageKindSpec,
    SqlKindSpec,
)
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.errors import (
    ConnectorValidationError,
    OperationExecutionTimeoutError,
)
from app.modules.connectors.domain.kinds import (
    DiscoveredOperation,
    KindExecutor,
    KindPlugin,
    ResolvedInstall,
)
from app.modules.connectors.infrastructure.kinds import build_kind_registry
from app.modules.connectors.infrastructure.kinds._install_validation import (
    validate_install_config,
)
from app.modules.connectors.infrastructure.kinds.brokered_kinds import ExpiryBasedRefresh
from app.modules.connectors.infrastructure.kinds.registry import KindRegistry
from app.modules.connectors.services.execution import KindDispatcher

ALL_KINDS = [
    ConnectorKind.COMPOSIO,
    ConnectorKind.PACKAGE,
    ConnectorKind.HTTP,
    ConnectorKind.SQL,
    ConnectorKind.MCP,
]


def _registry() -> KindRegistry:
    return build_kind_registry(
        composio_gateway=AsyncMock(), package_gateway=AsyncMock()
    )


def _install(kind: ConnectorKind, config: dict | None = None) -> ResolvedInstall:
    specs = {
        ConnectorKind.PACKAGE: PackageKindSpec(),
        ConnectorKind.SQL: SqlKindSpec(),
        ConnectorKind.MCP: McpKindSpec(),
    }
    return ResolvedInstall(
        connector_id="c",
        kind=kind,
        auth_config_id=uuid4(),
        organization_id=uuid4(),
        config=config or {},
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        spec=specs.get(kind, PackageKindSpec()),
    )


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.value)
def test_every_kind_resolves_to_a_plugin_with_an_executor(kind):
    plugin = _registry().get(kind)
    assert isinstance(plugin, KindPlugin)
    assert plugin.kind is kind
    assert isinstance(plugin.executor, KindExecutor)


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.value)
def test_every_kind_is_bounded_by_a_timeout(kind):
    # A new kind cannot silently run unbounded: the dispatcher always resolves
    # a deadline, falling back to the module default.
    assert KindDispatcher(_registry()).timeout_for(kind) > 0


def test_only_kinds_with_a_tenant_endpoint_discover():
    registry = _registry()
    discovering = {k for k in ALL_KINDS if registry.get(k).discoverer is not None}
    # Composio and vendored packages have catalog-fixed operation sets.
    assert discovering == {ConnectorKind.HTTP, ConnectorKind.MCP}


def test_unknown_kind_is_rejected_rather_than_silently_defaulted():
    with pytest.raises(ConnectorValidationError):
        _registry().get("carrier-pigeon")


@pytest.mark.asyncio
async def test_dispatch_reaches_the_registered_executor():
    gateway = AsyncMock()
    gateway.execute_operation.return_value = {"ok": True}
    registry = build_kind_registry(
        composio_gateway=AsyncMock(), package_gateway=gateway
    )
    dispatcher = KindDispatcher(registry)

    request = dispatcher.build_request(
        connector_id="slack",
        kind=ConnectorKind.PACKAGE,
        operation=ResolvedOperation(name="chat_post_message"),
        payload={"channel": "#general"},
        credentials={"access_token": "tok"},
        config={},
    )
    assert await dispatcher.execute(request) == {"ok": True}
    assert gateway.execute_operation.await_args.kwargs["operation_name"] == (
        "chat_post_message"
    )


@pytest.mark.asyncio
async def test_a_hanging_executor_is_cut_off_and_reported_as_a_timeout():
    class _Hangs:
        async def execute(self, request):
            await asyncio.sleep(30)

    dispatcher = KindDispatcher(
        KindRegistry(
            {ConnectorKind.MCP: KindPlugin(kind=ConnectorKind.MCP, executor=_Hangs())}
        )
    )
    request = dispatcher.build_request(
        connector_id="mcp",
        kind=ConnectorKind.MCP,
        operation=ResolvedOperation(name="slow_tool"),
        payload={},
        credentials={},
        config={},
    )
    object.__setattr__(request, "deadline_seconds", 0.05)

    with pytest.raises(OperationExecutionTimeoutError):
        await dispatcher.execute(request)


@pytest.mark.asyncio
async def test_discovery_is_bounded_too():
    # Discovery previously had no deadline at all, so an unresponsive MCP server
    # held the request that created the install open indefinitely.
    class _Hangs:
        async def discover(self, install, credentials):
            await asyncio.sleep(30)

    dispatcher = KindDispatcher(
        KindRegistry(
            {
                ConnectorKind.MCP: KindPlugin(
                    kind=ConnectorKind.MCP, executor=AsyncMock(), discoverer=_Hangs()
                )
            }
        )
    )
    from app.modules.connectors.config import connector_settings

    original = connector_settings.connector_discovery_timeout_seconds
    connector_settings.connector_discovery_timeout_seconds = 0.05
    try:
        with pytest.raises(OperationExecutionTimeoutError):
            await dispatcher.discover(_install(ConnectorKind.MCP))
    finally:
        connector_settings.connector_discovery_timeout_seconds = original


@pytest.mark.asyncio
async def test_a_static_kind_discovers_nothing_rather_than_erroring():
    dispatcher = KindDispatcher(_registry())
    assert await dispatcher.discover(_install(ConnectorKind.PACKAGE)) == []


@pytest.mark.asyncio
async def test_discovered_operations_keep_their_execution_descriptor():
    class _Finds:
        async def discover(self, install, credentials):
            return [
                DiscoveredOperation(
                    name="search", execution={"kind": "mcp", "tool_name": "Search"}
                )
            ]

    dispatcher = KindDispatcher(
        KindRegistry(
            {
                ConnectorKind.MCP: KindPlugin(
                    kind=ConnectorKind.MCP, executor=AsyncMock(), discoverer=_Finds()
                )
            }
        )
    )
    found = await dispatcher.discover(_install(ConnectorKind.MCP))
    # The descriptor carries the provider's real casing; the public name is
    # normalized, so losing it would make the operation unexecutable.
    assert found[0].execution["tool_name"] == "Search"


class TestInstallValidation:
    """Config validation now runs for every kind, including the tenant-written ones."""

    def test_unknown_keys_are_rejected_when_the_schema_closes_them(self):
        spec = McpKindSpec(
            auth_config_schema={
                "type": "object",
                "required": ["server_url"],
                "properties": {"server_url": {"type": "string"}},
                "additionalProperties": False,
            }
        )
        with pytest.raises(ConnectorValidationError) as excinfo:
            validate_install_config(
                spec, {"server_url": "https://x.test", "bearer_token": "secret"}
            )
        # The violation is reported, but never the offending value.
        assert "secret" not in str(excinfo.value)

    def test_missing_required_fields_are_reported_with_their_path(self):
        spec = SqlKindSpec(
            auth_config_schema={
                "type": "object",
                "required": ["host", "database"],
                "properties": {
                    "host": {"type": "string"},
                    "database": {"type": "string"},
                },
            }
        )
        with pytest.raises(ConnectorValidationError) as excinfo:
            validate_install_config(spec, {"host": "db.internal"})
        assert excinfo.value.details["violations"]

    def test_a_kind_declaring_no_schema_still_rejects_arbitrary_keys(self):
        with pytest.raises(ConnectorValidationError):
            validate_install_config(PackageKindSpec(), {"anything": "goes"})

    def test_valid_config_passes_through_unchanged(self):
        spec = McpKindSpec(
            auth_config_schema={
                "type": "object",
                "properties": {"server_url": {"type": "string"}},
            }
        )
        config = {"server_url": "https://mcp.test"}
        assert validate_install_config(spec, config) == config


class TestExpiryBasedRefresh:
    """Refresh is a predicate now, not something done before every single call."""

    def setup_method(self):
        self.policy = ExpiryBasedRefresh()
        self.now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

    def test_a_credential_with_no_expiry_is_never_proactively_refreshed(self):
        # API keys, bot tokens, and providers that report no expiry. Refreshing
        # these on every call is what made each execution pay a round trip.
        assert self.policy.refresh_due({"access_token": "t"}, now=self.now) is False

    def test_a_comfortably_valid_credential_is_left_alone(self):
        creds = {"expires_at": self.now + timedelta(hours=1)}
        assert self.policy.refresh_due(creds, now=self.now) is False

    def test_a_credential_inside_the_skew_window_is_refreshed(self):
        creds = {"expires_at": self.now + timedelta(seconds=30)}
        assert self.policy.refresh_due(creds, now=self.now) is True

    def test_an_already_expired_credential_is_refreshed(self):
        creds = {"expires_at": self.now - timedelta(minutes=5)}
        assert self.policy.refresh_due(creds, now=self.now) is True

    def test_an_iso_string_expiry_is_understood(self):
        creds = {"expires_at": (self.now - timedelta(minutes=1)).isoformat()}
        assert self.policy.refresh_due(creds, now=self.now) is True

    def test_a_naive_expiry_is_treated_as_utc_rather_than_crashing(self):
        creds = {"expires_at": (self.now + timedelta(hours=1)).replace(tzinfo=None)}
        assert self.policy.refresh_due(creds, now=self.now) is False

    def test_an_unparseable_expiry_does_not_raise(self):
        # A malformed stored credential must not take down every execution.
        assert self.policy.refresh_due({"expires_at": "not-a-date"}, now=self.now) is False
