"""What a connector kind has to provide.

Four narrow protocols rather than one wide one, because the kinds genuinely
differ: ``sql`` has no OAuth to implement and ``package`` has nothing to
discover. A kind supplies only the pieces it has, and the registry is the single
place that knows which those are.

Everything here is plain values. The execute phase deliberately runs with no
database connection held, so nothing in :class:`ExecutionRequest` may be
session-bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.connector import ConnectorKind, KindSpec
from app.modules.connectors.domain.connector_operation import ResolvedOperation


@dataclass(frozen=True, slots=True)
class ResolvedInstall:
    """One organization's install, resolved and decrypted.

    Replaces the pattern of ``model_copy``-ing a fake ``ConnectorEntity`` with
    runtime-only OAuth fields attached, which made it impossible to tell catalog
    data from per-install data at a glance.
    """

    connector_id: str
    kind: ConnectorKind
    auth_config_id: UUID
    organization_id: UUID
    config: dict[str, Any]
    config_source: AuthConfigSource
    spec: KindSpec
    composio_auth_config_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Everything the executor needs, carrying no session-bound state."""

    connector_id: str
    kind: ConnectorKind
    operation: ResolvedOperation
    payload: dict[str, Any]
    credentials: dict[str, Any]
    config: dict[str, Any]
    deadline_seconds: float
    # Only the package kind still needs these; they ride along until it is
    # migrated off the vendored client factory.
    auth_token: str | None = None
    api_url: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredOperation:
    """One operation found by interrogating a live install."""

    name: str
    display_name: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    execution: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class KindInstaller(Protocol):
    """Validates and normalizes an install's config before it is stored."""

    async def validate_install(
        self,
        *,
        spec: KindSpec,
        config: dict[str, Any],
        config_source: AuthConfigSource,
    ) -> dict[str, Any]: ...


@runtime_checkable
class KindDiscoverer(Protocol):
    """Turns a live install into its operation set."""

    async def discover(
        self, install: ResolvedInstall, credentials: dict[str, Any] | None
    ) -> list[DiscoveredOperation]: ...


@runtime_checkable
class KindExecutor(Protocol):
    """Runs one operation against the upstream."""

    async def execute(self, request: ExecutionRequest) -> Any: ...


@dataclass(frozen=True, slots=True)
class KindPlugin:
    """Everything registered for one kind."""

    kind: ConnectorKind
    executor: KindExecutor
    installer: KindInstaller | None = None
    # Refresh policy is deliberately *not* here. It was, as a per-kind
    # `authenticator`, and nothing ever read it: the decision runs through
    # `credential_freshness.credential_refresh_due`, which is expiry-driven and
    # kind-independent. Two copies of one rule, one of them dead, only misleads
    # a reader into thinking refresh is pluggable per kind.
    discoverer: KindDiscoverer | None = None
