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
from typing import Any, Protocol, runtime_checkable, Literal
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


#: Whatever a provider handed back. Genuinely unknown here -- it is the
#: provider's own JSON, or a `BinaryContentResult` for the five operations that
#: download a file -- and it is not narrowed until an operation's output schema
#: is applied further out. Named rather than written as a bare `Any` at each
#: site so the reason is stated once and reads as a decision.
#: Who a connector call presents as. `github_token_kind` says what a GitHub App
#: is *permitted* to do; this says what the caller *should be*, and they are not
#: the same question. An agent's operations act as the app so a schedule
#: outlives the person who set it up; pod publish, pod import and the sandbox's
#: `git`/`gh` act as the person, so the work is attributed to whoever owns the
#: repository. Defaulting to "user" keeps every caller that says nothing on the
#: behaviour it had before there was an app to be.
ActingIdentity = Literal["user", "app"]

ExecutionResult = Any


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
    #: The upstream tenant this *account* is bound to, when it has one --
    #: `accounts.external_ref`. Separate from `config`, which is the install's
    #: and therefore shared by every account on it: a GitHub App installed on
    #: two organizations gives their accounts different installations, so
    #: reading this from the install config would hand one org's token to the
    #: other. Generic here; what it means is the presenter's business.
    account_external_ref: str | None = None
    #: Who this call should present as. See `ActingIdentity`.
    act_as: ActingIdentity = "user"


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
