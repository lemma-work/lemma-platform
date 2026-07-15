from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping
from urllib.parse import urlparse, urlunparse

from agentbox.schemas import SandboxInternalStatus


EndpointProtocol = Literal["http", "websocket"]
TransientGateway = Literal["e2b"]


@dataclass(frozen=True)
class SandboxRef:
    """Stable logical and provider-native identity for one sandbox."""

    sandbox_id: str
    provider_id: str


@dataclass(frozen=True)
class ManagedSandbox:
    """Provider inventory item used by the lifecycle reconciler."""

    ref: SandboxRef
    status: SandboxInternalStatus
    generation: int = 0
    instance_id: str | None = None
    created_at: float | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    stable_release_identity: bool
    release_preserves_filesystem: bool
    private_egress_isolation: bool
    authenticated_http: bool
    authenticated_websocket: bool

    def diagnostic(self) -> dict[str, bool]:
        return {
            "stable_release_identity": self.stable_release_identity,
            "release_preserves_filesystem": self.release_preserves_filesystem,
            "private_egress_isolation": self.private_egress_isolation,
            "authenticated_http": self.authenticated_http,
            "authenticated_websocket": self.authenticated_websocket,
        }


@dataclass(frozen=True)
class ProviderCapacityPolicy:
    scope: str
    max_active: int


@dataclass(frozen=True)
class SandboxEndpoint:
    """A possibly authenticated endpoint for an app inside a sandbox.

    Cloud providers commonly return short-lived preview URLs that require
    provider-specific headers.  Keeping those details in this value lets the
    manager share one HTTP/WebSocket/runtime transport without importing a
    provider SDK or monkeypatching the proxy.
    """

    base_url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    websocket_query: Mapping[str, str] = field(default_factory=dict)
    websocket_subprotocols: tuple[str, ...] = ()
    expires_at: float | None = None
    instance_id: str | None = None
    provider_id: str | None = None
    transient_gateway: TransientGateway | None = None

    def url(self, *, protocol: EndpointProtocol = "http") -> str:
        parsed = urlparse(self.base_url)
        if protocol == "websocket":
            scheme = "wss" if parsed.scheme == "https" else "ws"
            return urlunparse(parsed._replace(scheme=scheme))
        return self.base_url.rstrip("/")
